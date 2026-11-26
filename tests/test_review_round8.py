"""Regressions from the eighth independent review."""

import asyncio

import pytest

from plainontology import api
from plainontology.cli import main
from plainontology.extract import extract_all
from plainontology.mcp_server import build_server
from plainontology.rename import rename
from plainontology.textformat import load, save

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_refresh_when_the_first_concept_goes_and_the_next_has_a_banner(tmp_path):
    _write(
        tmp_path,
        "s.sql",
        "CREATE TABLE authors (id integer PRIMARY KEY, name text);\n"
        "CREATE TABLE books (id integer PRIMARY KEY, title text, author_id integer REFERENCES authors(id));\n",
    )
    f = tmp_path / "lib.onto"
    api.init(f, [f"sqlite:{(tmp_path / 's.sql').as_posix()}"])
    f.write_text(
        f.read_text(encoding="utf-8").replace("concept Book ", "# Books: see the cataloguing wiki\n\nconcept Book "),
        encoding="utf-8",
    )
    _write(tmp_path, "s.sql", "CREATE TABLE books (id integer PRIMARY KEY, title text NOT NULL, author_name text);\n")
    api.refresh(f)  # used to refuse to write
    onto = load(f)
    assert list(onto.concepts) == ["Book"]
    assert onto.concepts["Book"].leading_comments == ["# Books: see the cataloguing wiki"]
    assert not api.compare(f)[2].has_drift


def test_rename_into_a_concept_with_a_banner(tmp_path):
    f = tmp_path / "r.onto"
    f.write_text(
        "source sqlite:x.sql\n\n"
        'concept Writer\n  note "a person\'s concept"\n\n'
        "# ===== Catalogue =====\n\n# One edition of a title\n"
        "concept Author [auto: sqlite table authors]\n  has id : integer key [auto]\n",
        encoding="utf-8",
    )
    onto = load(f)
    rename(onto, "Writer", "Author")
    save(f, onto)  # used to refuse to write
    author = load(f).concepts["Author"]
    assert author.first("note") is not None
    assert [c.strip() for c in author.leading_comments if c.strip()] == [
        "# ===== Catalogue =====",
        "# One edition of a title",
    ]


def test_column_zero_comment_among_property_comments_keeps_its_order(tmp_path):
    text = (
        "concept Book [auto: sqlite table books]\n"
        "  has title : text required [auto]\n"
        "  # title rules agreed with cataloguing:\n"
        "# FIXME(ana): confirm the max length\n"
        '    label "Title proper"\n'
    )
    f = tmp_path / "b.onto"
    f.write_text(text, encoding="utf-8")
    server = build_server(f, allow_proposals=True)
    asyncio.run(server.call_tool("propose", {"concept": "Book", "line": 'synonym "Volume"'}))
    children = [(c.kind, c.value.strip()) for c in load(f).concepts["Book"].prop("title").children]
    assert children == [
        ("comment", "title rules agreed with cataloguing:"),
        ("comment", "FIXME(ana): confirm the max length"),
        ("label", "Title proper"),
    ]


def test_mysql_rename_table_and_unknown_tables(tmp_path):
    _write(
        tmp_path,
        "r.sql",
        "CREATE TABLE `posts` (`id` bigint NOT NULL AUTO_INCREMENT PRIMARY KEY, `title` varchar(200) NOT NULL);\n"
        "CREATE TABLE `comments` (`id` bigint NOT NULL AUTO_INCREMENT PRIMARY KEY, `post_id` bigint NOT NULL,"
        " CONSTRAINT `fk_rails_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`));\n"
        "RENAME TABLE `posts` TO `articles`;\n"
        "ALTER TABLE `articles` ADD `published_at` datetime(6);\n"
        "ALTER TABLE ghost ADD COLUMN x int;\n",
    )
    onto = extract_all(["mysql:r.sql"], tmp_path)
    assert sorted(onto.concepts) == ["Article", "Comment"]
    assert onto.concepts["Article"].prop("published_at") is not None
    assert onto.concepts["Comment"].prop("post").target == "Article"
    assert any("ghost" in m for _c, m in onto.warnings)


def test_postgres_set_schema_rename_constraint_and_search_path_drop_index(tmp_path):
    _write(
        tmp_path,
        "p.sql",
        "CREATE SCHEMA archive;\nCREATE SCHEMA sales;\n"
        "CREATE TABLE orders (id int PRIMARY KEY, code text, CONSTRAINT orders_code_key UNIQUE (code));\n"
        "ALTER TABLE orders RENAME CONSTRAINT orders_code_key TO orders_code_uq;\n"
        "ALTER TABLE orders DROP CONSTRAINT orders_code_uq;\n"
        "ALTER TABLE orders SET SCHEMA archive;\n"
        "ALTER TABLE archive.orders ADD COLUMN moved_at timestamptz;\n"
        "CREATE TABLE sales.customers (id int PRIMARY KEY, email text);\n"
        "CREATE UNIQUE INDEX customers_email_idx ON sales.customers (email);\n"
        "SET search_path TO sales;\nDROP INDEX customers_email_idx;\n",
    )
    onto = extract_all(["postgres:p.sql"], tmp_path)
    assert sorted(onto.concepts) == ["ArchiveOrder", "SalesCustomer"]
    order = onto.concepts["ArchiveOrder"]
    assert order.prop("moved_at") is not None and "unique" not in order.prop("code").flags
    assert "unique" not in onto.concepts["SalesCustomer"].prop("email").flags
    assert onto.warnings == []


def test_mysql_float_unsigned_comments_and_multi_column_keys(tmp_path):
    _write(
        tmp_path,
        "m.sql",
        "CREATE TABLE m (id int PRIMARY KEY, w float unsigned COMMENT 'stored as FLOAT UNSIGNED in kg',"
        " a int, b int, UNIQUE KEY ab (a, b), c int, UNIQUE KEY ci (c)) COMMENT='was real unsigned before 2020';\n"
        "ALTER TABLE m DROP COLUMN b;\nALTER TABLE m RENAME INDEX ci TO c_idx;\nALTER TABLE m DROP INDEX c_idx;\n",
    )
    m = extract_all(["mysql:m.sql"], tmp_path).concepts["M"]
    assert m.prop("w").value == "stored as FLOAT UNSIGNED in kg" and m.prop("w").child("min").value == "0"
    assert m.definition() == "was real unsigned before 2020"
    assert "unique" in m.prop("a").flags and "unique" not in m.prop("c").flags


def test_onto_file_outside_the_source_root_is_refused(tmp_path, monkeypatch, capsys):
    inside = tmp_path / "repo"
    inside.mkdir()
    outside = tmp_path / "secret.onto"
    outside.write_text("concept A\n", encoding="utf-8")
    monkeypatch.setenv("PLAINONTO_SOURCE_ROOT", str(inside))
    assert main(["check", str(outside)]) == 2
    assert "PLAINONTO_SOURCE_ROOT" in capsys.readouterr().err

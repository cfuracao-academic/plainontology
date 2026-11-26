"""Regressions from the seventh independent review."""

import asyncio

import pytest

from plainontology import api
from plainontology.check import check
from plainontology.extract import extract_all
from plainontology.mcp_server import build_server
from plainontology.textformat import dumps, load, loads

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


def _init(tmp_path, sql):
    _write(tmp_path, "s.sql", sql)
    f = tmp_path / "lib.onto"
    api.init(f, [f"sqlite:{(tmp_path / 's.sql').as_posix()}"])
    return f


def test_comment_above_the_next_concept_stays_with_it(tmp_path):
    f = _init(
        tmp_path,
        "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);\nCREATE TABLE tags (id INTEGER PRIMARY KEY);\n",
    )
    text = f.read_text(encoding="utf-8").replace(
        "  has title : text [auto]\n\nconcept Tag",
        "  has title : text [auto]\n  # TODO: add subtitle\n\n# Tags are free text; never trust them.\nconcept Tag",
    )
    f.write_text(text, encoding="utf-8")
    _write(
        tmp_path,
        "s.sql",
        "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, year INT);\n"
        "CREATE TABLE tags (id INTEGER PRIMARY KEY);\n",
    )
    api.refresh(f)
    onto = load(f)
    assert onto.concepts["Tag"].leading_comments == ["# Tags are free text; never trust them."]
    assert "# Tags" not in "".join(s.value for s in onto.concepts["Book"].all("comment"))
    assert onto.concepts["Book"].prop("year") is not None


@pytest.mark.parametrize(
    "body",
    [
        "concept Loan\n    has due : date required\n    # --- borrower fields ---\n    borrower -> Book\n",
        "concept Loan\n\thas due : date required\n\t# --- borrower fields ---\n\tborrower -> Book\n",
    ],
)
def test_comment_between_properties_stays_on_the_concept(tmp_path, body):
    f = tmp_path / "l.onto"
    f.write_text(body, encoding="utf-8")
    server = build_server(f, allow_proposals=True)
    asyncio.run(server.call_tool("propose", {"concept": "Loan", "line": 'synonym "Borrowing"'}))
    loan = load(f).concepts["Loan"]
    assert loan.prop("due").children == []
    assert [s.value.strip() for s in loan.all("comment")] == ["--- borrower fields ---"]


def test_column_zero_comment_above_a_dropped_property_stays_in_its_concept(tmp_path):
    f = _init(
        tmp_path,
        "CREATE TABLE books (id INTEGER PRIMARY KEY, legacy TEXT);\nCREATE TABLE tags (id INTEGER PRIMARY KEY);\n",
    )
    f.write_text(
        f.read_text(encoding="utf-8").replace(
            "  has legacy : text [auto]\n", "# old field below\n  has legacy : text [auto]\n"
        ),
        encoding="utf-8",
    )
    _write(
        tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY);\nCREATE TABLE tags (id INTEGER PRIMARY KEY);\n"
    )
    api.refresh(f)
    onto = load(f)
    assert onto.concepts["Tag"].leading_comments == []
    assert [s.value.strip() for s in onto.concepts["Book"].all("comment")] == ["old field below"]


def test_lines_handed_over_are_merged_not_appended(tmp_path):
    f = _init(tmp_path, "CREATE TABLE books (id INTEGER PRIMARY KEY, pages INTEGER CHECK (pages <= 5000));\n")
    f.write_text(
        f.read_text(encoding="utf-8").replace(
            "  has pages : integer [auto]\n",
            "  has pages : integer required\n  has pages : integer [auto]\n    max 3000\n",
        ),
        encoding="utf-8",
    )
    api.refresh(f)
    onto = load(f)
    assert [c.value for c in onto.concepts["Book"].prop("pages").children] == ["3000"]
    assert not [i for i in check(onto) if i.level == "error"]
    assert not api.compare(f)[2].has_drift


def test_constraint_bookkeeping_after_drops_renames_and_schemas(tmp_path):
    _write(
        tmp_path,
        "m.sql",
        "CREATE TABLE members (id int PRIMARY KEY, email text UNIQUE);\n"
        "DROP TABLE members;\n"
        "CREATE TABLE members (id int PRIMARY KEY, email text, CONSTRAINT members_email_uq UNIQUE (email));\n"
        "ALTER TABLE members DROP CONSTRAINT members_email_uq;\n"
        "CREATE TABLE people (id int PRIMARY KEY, email text UNIQUE);\n"
        "ALTER TABLE people RENAME COLUMN email TO mail;\n"
        "ALTER TABLE people DROP CONSTRAINT people_email_key;\n"
        "CREATE SCHEMA sales;\n"
        "CREATE TABLE public.orders (id int PRIMARY KEY, code text);\n"
        "CREATE TABLE sales.orders (id int PRIMARY KEY, code text);\n"
        "CREATE UNIQUE INDEX ON public.orders (code);\nCREATE UNIQUE INDEX ON sales.orders (code);\n"
        "DROP INDEX sales.orders_code_idx;\n",
    )
    c = extract_all(["postgres:m.sql"], tmp_path).concepts
    assert "unique" not in c["Member"].prop("email").flags
    assert "unique" not in c["Person"].prop("mail").flags
    assert "unique" in c["Order"].prop("code").flags and "unique" not in c["SalesOrder"].prop("code").flags


def test_or_is_not_null_is_not_a_rule(tmp_path):
    _write(
        tmp_path,
        "c.sql",
        "CREATE TABLE items (id int PRIMARY KEY, score int CHECK (score > 0 OR score IS NOT NULL));\n",
    )
    assert extract_all(["postgres:c.sql"], tmp_path).concepts["Item"].prop("score").children == []


def test_network_source_is_refused_before_it_is_touched(tmp_path, monkeypatch):
    from pathlib import Path

    from plainontology.extract import within_root

    monkeypatch.setenv("PLAINONTO_SOURCE_ROOT", str(tmp_path))
    touched = []
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: touched.append(self) or self)
    with pytest.raises(ValueError, match="network path"):
        within_root(Path("//attacker.invalid/share/x.json"))
    assert touched == []


def test_mysql_float_unsigned_column_is_kept(tmp_path):
    _write(tmp_path, "f.sql", "CREATE TABLE m (id int PRIMARY KEY, w float unsigned, d double(8,2) unsigned);\n")
    onto = extract_all(["mysql:f.sql"], tmp_path)
    m = onto.concepts["M"]
    assert m.prop("w").target == "decimal" and m.prop("d") is not None
    assert [w for _c, w in onto.warnings] == []


def test_leaves_stays_a_table_of_leave_records():
    from plainontology.naming import singular

    assert singular("leaves") == "leave" and singular("shelves") == "shelf"


def test_a_comment_line_never_moves_between_parents_on_write():
    text = "concept A\n  has x : text\n# last\n"
    onto = loads(text)
    again = loads(dumps(onto))
    assert [s.kind for s in again.concepts["A"].statements] == [s.kind for s in onto.concepts["A"].statements]

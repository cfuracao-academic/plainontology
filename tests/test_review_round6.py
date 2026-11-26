"""Regressions from the sixth independent review."""

import asyncio
import json

import pytest

from plainontology import api
from plainontology.extract import extract_all
from plainontology.mcp_server import build_server
from plainontology.naming import singular
from plainontology.textformat import _shape, dumps, load, loads, save

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


FOUR_SPACES = (
    "source sqlite:{src}\n\n"
    "concept Book [auto: sqlite table books]\n"
    "    has id : integer key [auto]\n"
    "    has title : text required [auto]\n"
    '    definition "A title the library holds."\n'
    '    synonym "Volume"\n'
)


def _four_space_file(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL);\n")
    f = tmp_path / "lib.onto"
    f.write_text(FOUR_SPACES.format(src=(tmp_path / "s.sql").as_posix()), encoding="utf-8")
    return f


def test_refresh_of_a_four_space_file_keeps_concept_lines_on_the_concept(tmp_path):
    f = _four_space_file(tmp_path)
    _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL, pages INTEGER);\n")
    api.refresh(f)
    book = load(f).concepts["Book"]
    assert book.first("definition") is not None and book.first("synonym") is not None
    assert book.prop("title").children == []


def test_agent_proposal_keeps_concept_lines_on_the_concept(tmp_path):
    f = _four_space_file(tmp_path)
    server = build_server(f, allow_proposals=True)
    asyncio.run(server.call_tool("propose", {"concept": "Book", "line": 'label "Book"@fr'}))
    book = load(f).concepts["Book"]
    assert book.first("definition") is not None and book.prop("title").children == []


def test_tab_indented_file_refreshes(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE racks (id INTEGER PRIMARY KEY);\n")
    f = tmp_path / "t.onto"
    f.write_text(
        f"source sqlite:{(tmp_path / 's.sql').as_posix()}\n\n"
        'concept Rack [auto: sqlite table racks]\n\thas id : integer key [auto]\n\tnote "oak"\n',
        encoding="utf-8",
    )
    _write(tmp_path, "s.sql", "CREATE TABLE racks (id INTEGER PRIMARY KEY, room TEXT);\n")
    assert api.refresh(f).has_drift
    rack = load(f).concepts["Rack"]
    assert rack.prop("room") is not None and rack.first("note") is not None


def test_save_refuses_a_write_that_would_change_the_structure(tmp_path, monkeypatch):
    import plainontology.textformat as tf

    onto = loads('concept A\n  has x : text\n  note "n"\n')
    monkeypatch.setattr(tf, "dumps", lambda o: 'concept A\n  has x : text\n    note "n"\n')
    with pytest.raises(ValueError, match="different concept or property"):
        save(tmp_path / "a.onto", onto)
    assert not (tmp_path / "a.onto").exists()


def test_comments_keep_their_order(tmp_path):
    for text in (
        "concept C0\n  has a : text\n# col0 comment\n  # indented comment\n  is_a Other\n",
        "concept B\n  has a : text\n  # TODO: add subtitle\n\n# about tags\nconcept Tag\n  has b : text\n",
    ):
        assert dumps(loads(text)) == text


def test_unsigned_follows_change_and_modify(tmp_path):
    _write(
        tmp_path,
        "m.sql",
        "CREATE TABLE t (id int NOT NULL, n int unsigned, a int unsigned, b int unsigned CHECK (b >= 5), "
        "PRIMARY KEY (id));\nALTER TABLE t CHANGE n m bigint;\nALTER TABLE t MODIFY a int;\n",
    )
    t = extract_all(["mysql:m.sql"], tmp_path).concepts["T"]
    assert t.prop("m").children == [] and t.prop("a").children == []
    assert t.prop("b").child("min").value == "5"


def test_drop_index_default_names_and_remaining_uniqueness(tmp_path):
    _write(
        tmp_path,
        "i.sql",
        "CREATE TABLE people (id int PRIMARY KEY, m int, email text);\n"
        "CREATE UNIQUE INDEX ON people (m, email);\nDROP INDEX CONCURRENTLY people_m_email_idx;\n"
        "CREATE TABLE u (id int PRIMARY KEY, email text UNIQUE);\n"
        "CREATE UNIQUE INDEX u_email_lower ON u (email);\nDROP INDEX u_email_lower;\n",
    )
    onto = extract_all(["postgres:i.sql"], tmp_path)
    assert onto.concepts["Person"].first("unique") is None
    assert "unique" in onto.concepts["U"].prop("email").flags


def test_checks_allowing_null_and_reversed_lengths(tmp_path):
    _write(
        tmp_path,
        "c.sql",
        "CREATE TABLE c (id int PRIMARY KEY, f text CHECK (f IN ('a','b') OR f IS NULL),"
        " e int CHECK (e > 0 AND e <= 10 OR e IS NULL), s text CHECK (8 = char_length(s)),"
        " g int, h int, CHECK (h > 0 OR g IS NULL));\n",
    )
    c = extract_all(["postgres:c.sql"], tmp_path).concepts["C"]
    assert c.prop("f").target == "enum(a|b)"
    assert c.prop("e").child("min").value == "1" and c.prop("e").child("max").value == "10"
    assert c.prop("s").child("length").value == "8..8"
    assert c.prop("h").children == [] and any("g IS NULL" in s.value for s in c.all("note"))


def test_kept_property_names_the_possible_rename(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY, isbn TEXT);\n")
    f = tmp_path / "b.onto"
    api.init(f, [f"sqlite:{(tmp_path / 's.sql').as_posix()}"])
    f.write_text(
        f.read_text(encoding="utf-8").replace(
            "  has isbn : text [auto]\n", '  has isbn : text [auto]\n    note "13"\n'
        ),
        encoding="utf-8",
    )
    _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY, isbn13 TEXT, subtitle TEXT);\n")
    out = api.compare(f)[2].render()
    assert "if it was renamed to isbn13 or subtitle, run: plainonto rename FILE Book.isbn isbn13" in out


def test_absolute_and_network_references_are_not_followed(tmp_path):
    schema = {
        "type": "object",
        "properties": {"a": {"$ref": "//host/share/x.json"}, "b": {"$ref": "C:/x.json"}, "c": {"$ref": "/etc/x.json"}},
    }
    _write(tmp_path, "s.json", json.dumps(schema))
    onto = extract_all(["jsonschema:s.json"], tmp_path)
    assert sum("not relative to the schema" in m for _c, m in onto.warnings) == 3


@pytest.mark.parametrize(
    ("plural", "single"),
    [
        ("menus", "menu"),
        ("analytics", "analytics"),
        ("diagnoses", "diagnosis"),
        ("vertices", "vertex"),
        ("shelves", "shelf"),
        ("archives", "archive"),
    ],
)
def test_more_singular_names(plural, single):
    assert singular(plural) == single


def test_shape_includes_where_comments_sit():
    a = loads("concept A\n  has x : text\n  # c\n  has y : text\n")  # a comment on the concept
    b = loads("concept A\n  has x : text\n    # c\n  has y : text\n")  # a comment under x
    assert _shape(a) != _shape(b)
    assert _shape(loads(dumps(a))) == _shape(a) and _shape(loads(dumps(b))) == _shape(b)

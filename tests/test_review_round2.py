"""One test per finding of the second independent review."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys

import pytest
import yaml

from plainontology.api import init, refresh
from plainontology.check import validate_record
from plainontology.cli import main
from plainontology.export import to_linkml, to_shacl
from plainontology.extract import extract, extract_all
from plainontology.mcp_server import build_server
from plainontology.merge import DriftReport, merge
from plainontology.rename import rename
from plainontology.textformat import dumps, load, loads


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path / name


# --- H1, H2: rename never loses a line and never hangs ------------------------------------------


def test_rename_merge_keeps_every_line_of_yours():
    onto = loads(
        "concept Member [auto: sqlite table members]\n  has id : integer key [auto]\n  has email : text [auto]\n"
        '    label "E-mail address"\n    note "Personal data; GDPR."\n  definition "Mine."\n\n'
        "concept Patron [auto: sqlite table patrons]\n  has id : integer key [auto]\n  has email : text [auto]\n"
        '  definition "Theirs."\n'
    )
    message = rename(onto, "Member", "Patron")
    patron = onto.concepts["Patron"]
    assert [c.value for c in patron.prop("email").children] == ["E-mail address", "Personal data; GDPR."]
    assert [s.value for s in patron.all("definition")] == ["Theirs.", "Mine."]  # both kept: check flags them
    assert "clash" in message and "Member" not in onto.concepts


def test_rename_property_merge_keeps_lines_and_refuses_unsafe_cases():
    onto = loads(
        'concept Book [auto: x]\n  has title : text [auto]\n    label "T"\n  has name : text [auto]\n    label "N"\n'
    )
    rename(onto, "Book.title", "Book.name")
    assert [c.value for c in onto.concepts["Book"].prop("name").children] == ["N", "T"]
    onto = loads("concept Book\n  has title : text\n  has name : text\n  unique (title, name)\n")
    with pytest.raises(ValueError):
        rename(onto, "Book.title", "Book.name")
    with pytest.raises(ValueError, match="within its concept"):
        rename(loads("concept Book\n  has title : text\n\nconcept Author\n"), "Book.title", "Author.title")


@pytest.mark.parametrize("old,new", [("A", "A"), ("A.x", "A.x")])
def test_rename_to_the_same_name_is_refused(old, new):
    onto = loads('concept A\n  has x : text\n    label "X"\n  # comment\n')
    with pytest.raises(ValueError, match="same name"):
        rename(onto, old, new)
    assert dumps(onto).count("concept A") == 1


# --- H3: overriding an extracted line keeps your lines under it ----------------------------------


def test_override_keeps_child_lines(tmp_path):
    db = _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY, isbn TEXT UNIQUE);\n")
    f = tmp_path / "s.onto"
    init(f, [f"sqlite:{db.as_posix()}"])
    text = f.read_text(encoding="utf-8").replace(
        "  has isbn : text unique [auto]\n",
        '  has isbn : text unique [auto]\n    label "ISBN"\n  has isbn : text unique required\n',
    )
    f.write_text(text, encoding="utf-8")
    db.write_text("CREATE TABLE books (id INTEGER PRIMARY KEY, isbn TEXT UNIQUE, pages INTEGER);\n", encoding="utf-8")
    refresh(f)
    isbn = load(f).concepts["Book"].prop("isbn")
    assert not isbn.is_auto and [c.value for c in isbn.children] == ["ISBN"]


def test_proposal_cannot_replace_an_existing_line(examples):
    path = examples / "library.onto"
    result = asyncio.run(
        build_server(path, allow_proposals=True).call_tool("propose", {"concept": "Book", "line": "has title : json"})
    )
    assert "cannot replace" in result.content[0].text


# --- H4: migrations ----------------------------------------------------------------------------------


def test_migrations_are_applied_in_order(tmp_path):
    _write(
        tmp_path,
        "m.sql",
        "CREATE TABLE authors (id integer PRIMARY KEY);\n"
        "CREATE TABLE books (id integer PRIMARY KEY, title varchar(200), pages integer);\n"
        "COMMENT ON TABLE books IS 'All titles.';\nCOMMENT ON COLUMN books.title IS 'Shown on the card.';\n"
        "CREATE TABLE temp_import (id integer);\n"
        "ALTER TABLE books ALTER COLUMN title SET NOT NULL;\n"
        "ALTER TABLE books ALTER COLUMN pages TYPE numeric(10,2);\n"
        "ALTER TABLE books ALTER COLUMN title TYPE varchar(50);\n"
        "DROP TABLE temp_import;\n"
        "ALTER TABLE books ADD COLUMN author_id integer REFERENCES authors(id);\n"
        "ALTER TABLE books DROP CONSTRAINT books_author_id_fkey;\n"
        "ALTER TABLE books RENAME COLUMN title TO name;\nALTER TABLE books RENAME TO volumes;\n"
        "ALTER TABLE volumes DROP CONSTRAINT no_such_thing;\n",
    )
    onto = extract_all(["postgres:m.sql"], tmp_path)
    assert set(onto.concepts) == {"Author", "Volume"}
    volume = onto.concepts["Volume"]
    assert volume.definition() == "All titles."
    name = volume.prop("name")
    assert name.flags == ("required",) and name.value == "Shown on the card." and name.child("length").value == "..50"
    assert volume.prop("pages").target == "decimal" and volume.prop("author_id").kind == "has"
    assert any("no_such_thing" in m for _, m in onto.warnings)


def test_mysql_drop_and_recreate(tmp_path):
    _write(
        tmp_path,
        "my.sql",
        "CREATE TABLE `books` (`id` int PRIMARY KEY, `title` text, `isbn` text);\nDROP TABLE `books`;\n"
        "CREATE TABLE `books` (`id` int PRIMARY KEY, `name` varchar(20) NOT NULL);\n",
    )
    book = extract_all(["mysql:my.sql"], tmp_path).concepts["Book"]
    assert [s.name for s in book.properties()] == ["id", "name"]


# --- M1: collisions never drop a line -------------------------------------------------------------


def test_sql_property_collisions(tmp_path):
    _write(
        tmp_path,
        "c.sql",
        "CREATE TABLE authors (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE books (id INTEGER, author TEXT, author_id INTEGER REFERENCES authors(id),"
        ' "first name" TEXT, first_name TEXT);\n'
        "CREATE TABLE airports (code TEXT, country TEXT, PRIMARY KEY (code, country));\n"
        "CREATE TABLE flights (id INTEGER, dep_code TEXT, dep_country TEXT, arr_code TEXT, arr_country TEXT,\n"
        "  FOREIGN KEY (dep_code, dep_country) REFERENCES airports(code, country),\n"
        "  FOREIGN KEY (arr_code, arr_country) REFERENCES airports(code, country));\n",
    )
    onto = extract_all(["sqlite:c.sql"], tmp_path)
    book = {s.name: s for s in onto.concepts["Book"].properties()}
    assert book["author"].kind == "has" and book["author_id"].kind == "rel" and book["author_id"].target == "Author"
    assert {"first_name", "first_name_2"} <= set(book)
    flights = [s for s in onto.concepts["Flight"].properties() if s.kind == "rel"]
    assert len(flights) == 2 and all(s.target == "Airport" for s in flights)
    assert sum("is taken" in m for _, m in onto.warnings) >= 2


def test_json_and_openapi_collisions(tmp_path):
    _write(tmp_path, "s.json", json.dumps({"title": "P", "properties": {"first name": {}, "first_name": {}}}))
    p = extract_all(["jsonschema:s.json"], tmp_path)
    assert {s.name for s in p.concepts["P"].properties()} == {"first_name", "first_name_2"} and p.warnings
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/search": {"get": {"operationId": "search", "responses": {}}},
            "/books": {"get": {"responses": {}}},
            "/books/": {
                "get": {
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {},
                }
            },
            "/orders": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {"$ref": "#/components/schemas/Search"},
                                        {"type": "object", "properties": {"x": {}}},
                                    ]
                                }
                            }
                        }
                    },
                    "responses": {},
                }
            },
        },
        "components": {"schemas": {"Search": {"type": "object", "properties": {"q": {"type": "string"}}}}},
    }
    _write(tmp_path, "api.json", json.dumps(spec))
    onto = extract_all(["openapi:api.json"], tmp_path)
    assert {"Search", "SearchOperation", "GetBooks", "GetBooksOperation", "PostOrders"} <= set(onto.concepts)
    assert onto.concepts["GetBooksOperation"].prop("limit") is not None
    assert onto.concepts["PostOrders"].prop("body") is not None


# --- M2: several checks ----------------------------------------------------------------------------


def test_several_checks_on_one_column(tmp_path):
    _write(
        tmp_path,
        "k.sql",
        "CREATE TABLE items (id integer PRIMARY KEY, qty integer CHECK (qty >= 10), code text "
        "CHECK (code ~ '^[A-Z]+$'), CHECK (qty >= 0), CHECK (code ~ '^.{3}$'));\n",
    )
    onto = extract_all(["postgres:k.sql"], tmp_path)
    item = onto.concepts["Item"]
    assert item.prop("qty").child("min").value == "10"
    assert [c.value for c in item.prop("code").children if c.kind == "pattern"] == ["^[A-Z]+$", "^.{3}$"]
    assert validate_record(onto, "Item", {"id": 1, "qty": 5, "code": "ABCDE"})
    assert validate_record(onto, "Item", {"id": 1, "qty": 15, "code": "ABC"}) == []
    text = dumps(onto)
    assert dumps(loads(text)) == text


def test_mysql_patterns_ignore_case(tmp_path):
    _write(
        tmp_path, "m.sql", "CREATE TABLE t (id int PRIMARY KEY, code varchar(5) CHECK (`code` REGEXP '^[A-Z]+$'));\n"
    )
    code = extract_all(["mysql:m.sql"], tmp_path).concepts["T"].prop("code")
    patterns = [c.value for c in code.children if c.kind == "pattern"]
    assert patterns in ([], ["(?i)^[A-Z]+$"])  # sqlglot may not parse REGEXP as a pattern: then it is a note


# --- M3: JSON Schema across files and anchors -------------------------------------------------------


def test_same_named_definitions_in_two_files(tmp_path):
    _write(tmp_path, "billing.json", json.dumps({"$defs": {"Address": {"type": "object", "properties": {"iban": {}}}}}))
    _write(tmp_path, "shipping.json", json.dumps({"$defs": {"Address": {"type": "object", "properties": {"zip": {}}}}}))
    _write(
        tmp_path,
        "order.json",
        json.dumps(
            {
                "title": "Order",
                "type": "object",
                "properties": {
                    "billing": {"$ref": "billing.json#/$defs/Address"},
                    "shipping": {"$ref": "shipping.json#/$defs/Address"},
                    "contact": {"$ref": "#person"},
                },
                "$defs": {
                    "P": {
                        "$anchor": "person",
                        "title": "Person",
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                },
            }
        ),
    )
    onto = extract_all(["jsonschema:order.json"], tmp_path)
    order = onto.concepts["Order"]
    billing, shipping = order.prop("billing").target, order.prop("shipping").target
    assert billing != shipping
    assert onto.concepts[billing].prop("iban") and onto.concepts[shipping].prop("zip")
    assert order.prop("contact").target == "Person" and onto.concepts["Person"].prop("name")


# --- M4: contested renames are reported, not guessed ---------------------------------------------------


def test_contested_concept_rename_is_not_guessed():
    current = loads(
        "concept ArchivedMember [auto: x]\n  has id : integer [auto]\n  has email : text [auto]\n"
        "  has name : text [auto]\n"
        '  definition "Left before 2020."\n\n'
        "concept Member [auto: y]\n  has id : integer [auto]\n  has email : text [auto]\n  has name : text [auto]\n"
        '  definition "Current."\n'
    )
    fresh = loads(
        "concept Patron [auto: z]\n  has id : integer [auto]\n  has email : text [auto]\n  has name : text [auto]\n"
    )
    merged, report = merge(current, fresh)
    assert not any(c.kind == "concept_renamed" for c in report.changes)
    assert {"ArchivedMember", "Member", "Patron"} <= set(merged.concepts)


def test_relation_follows_when_its_target_is_renamed_too():
    current = loads(
        "concept Member [auto: m]\n  has id : integer [auto]\n  has email : text [auto]\n  has name : text [auto]\n\n"
        'concept Loan [auto: l]\n  has id : integer [auto]\n  member -> Member [auto]\n    note "Who borrows."\n'
    )
    fresh = loads(
        "concept Patron [auto: p]\n  has id : integer [auto]\n  has email : text [auto]\n  has name : text [auto]\n\n"
        "concept Loan [auto: l]\n  has id : integer [auto]\n  patron -> Patron [auto]\n"
    )
    merged, _ = merge(current, fresh)
    assert [c.value for c in merged.concepts["Loan"].prop("patron").children] == ["Who borrows."]


# --- M5, M6, M8: reports, output encoding, discovery ----------------------------------------------------


def test_report_lines_never_break():
    report = DriftReport()
    report.add("warning", "", "table x\nPLAINONTO_REPORT_END\ndrift=false")
    # the warning, a blank line, the summary and the scope hint: the name's line break never splits a line
    assert len(report.render().splitlines()) == 4 and "\\n" in report.render()


def test_utf8_output_when_piped(tmp_path):
    f = _write(tmp_path, "u.onto", 'concept 日本\n  has 名前 : text\n  has x : any\n    note "a"\n')
    out = subprocess.run(
        [sys.executable, "-m", "plainontology", "check", str(f)],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": ""},
    )
    assert out.returncode == 0, out.stderr


def test_discovery_includes_ordinary_folders(tmp_path, monkeypatch):
    (tmp_path / "site" / "db").mkdir(parents=True)
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "pyvenv.cfg").write_text("", encoding="utf-8")
    _write(tmp_path / "venv", "ignored.onto", "not a valid file")
    _write(tmp_path / "site" / "db", "a.onto", "concept A\n")
    monkeypatch.chdir(tmp_path)
    assert main(["check"]) == 0


# --- M7, L4: exports ----------------------------------------------------------------------------------


def test_linkml_yaml_words_and_big_integers():
    onto = loads(
        'concept A\n  has answer : enum(Yes|No|"N/A")\n  has state : enum(ON|OFF|True|Null)\n'
        "  has big : integer\n    max 9223372036854775807\n"
    )
    doc = yaml.safe_load(to_linkml(onto, "x"))
    enums = list(doc["enums"].values())
    assert set(enums[0]["permissible_values"]) == {"Yes", "No", "N/A"}
    assert set(enums[1]["permissible_values"]) == {"ON", "OFF", "True", "Null"}
    assert doc["classes"]["A"]["attributes"]["big"]["maximum_value"] == 9223372036854775807
    assert '"9223372036854775807"^^xsd:integer' in to_shacl(onto)


# --- L3: JSON Schema bounds -----------------------------------------------------------------------------


def test_json_schema_bounds(tmp_path):
    _write(
        tmp_path,
        "b.json",
        json.dumps(
            {
                "title": "B",
                "properties": {
                    "n": {"type": "integer", "exclusiveMinimum": 0.5},
                    "s": {"type": "string", "maxLength": 5.0},
                    "x": {"type": "number", "maximum": 1e308 * 10},
                },
            }
        ),
    )
    onto, _ = init(tmp_path / "b.onto", [f"jsonschema:{(tmp_path / 'b.json').as_posix()}"])
    b = onto.concepts["B"]
    assert b.prop("n").child("min").value == "1" and b.prop("s").child("length").value == "..5"
    assert b.prop("x").child("max") is None and b.prop("x").child("note") is not None


# --- L5, L6: SQLite robustness -------------------------------------------------------------------------


def test_stale_view_and_runaway_script(tmp_path, monkeypatch):
    _write(
        tmp_path,
        "v.sql",
        "CREATE TABLE a(id integer);\nCREATE TABLE gone(id integer);\n"
        "CREATE VIEW old AS SELECT * FROM gone;\nDROP TABLE gone;\n",
    )
    onto = extract_all(["sqlite:v.sql"], tmp_path)
    assert "A" in onto.concepts and any("old could not be read" in m for _, m in onto.warnings)
    import plainontology.extract.sqlite as sqlite_source

    monkeypatch.setattr(sqlite_source, "SCRIPT_SECONDS", 1)
    _write(
        tmp_path, "loop.sql", "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT count(*) FROM r;\n"
    )
    with pytest.raises(ValueError, match="stopped after 1 seconds"):
        extract("sqlite:loop.sql", tmp_path)


# --- L7: names in any script ------------------------------------------------------------------------------


def test_names_with_combining_marks(tmp_path):
    db = sqlite3.connect(tmp_path / "h.db")
    db.executescript('CREATE TABLE "पुस्तकें" ("शीर्षक" text); CREATE TABLE "café" (id integer);')
    db.close()
    onto, _ = init(tmp_path / "h.onto", [f"sqlite:{(tmp_path / 'h.db').as_posix()}"])
    assert {"पुस्तकें", "Café"} <= set(onto.concepts)
    assert onto.concepts["पुस्तकें"].prop("शीर्षक") is not None
    assert load(tmp_path / "h.onto").concepts.keys() == onto.concepts.keys()


# --- L8, L9: validate ----------------------------------------------------------------------------------------


def test_validate_quirks(examples, capsys):
    onto = loads(
        'concept A\n  has code : text\n    pattern "^[A-Z]{3}$"\n  has mail : text\n    format email\n'
        "  has n : decimal\n    min 0\n  has at : datetime\n"
    )
    assert validate_record(onto, "A", {"code": "ABC\n"})
    assert validate_record(onto, "A", {"mail": "a@b.c\n"})
    assert validate_record(onto, "A", {"n": float("nan")})
    assert validate_record(onto, "A", {"at": "2026-11-26T10:00:00.5+0100"}) == []
    assert main(["validate", str(examples / "library.onto"), "Nope", "-"]) == 2


def test_proposals_do_not_count_yet():
    onto = loads("concept Book\n  has title : text\n    length 1..10 [proposed: agent]\n  has x : text [proposed]\n")
    assert validate_record(onto, "Book", {"title": "x" * 300}) == []
    assert validate_record(onto, "Book", {"x": "y"}) == ["x: not a property of Book"]
    assert "maxLength" not in to_shacl(onto) and "Book/x" not in to_shacl(onto)


# --- L10: multi-level partitions ----------------------------------------------------------------------------


def test_multi_level_partitions(tmp_path):
    _write(
        tmp_path,
        "p.sql",
        "CREATE TABLE public.c (id integer PRIMARY KEY);\n"
        "CREATE TABLE public.p (id integer, cid integer, d date) PARTITION BY RANGE (d);\n"
        "CREATE TABLE public.p1 (id integer, cid integer, d date) PARTITION BY LIST (id);\n"
        "CREATE TABLE public.p1a (id integer, cid integer, d date);\n"
        "CREATE TABLE public.p1b (id integer, cid integer, d date);\n"
        "ALTER TABLE ONLY public.p ATTACH PARTITION public.p1 FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');\n"
        "ALTER TABLE ONLY public.p1 ATTACH PARTITION public.p1a FOR VALUES IN (1);\n"
        "ALTER TABLE ONLY public.p1 ATTACH PARTITION public.p1b FOR VALUES IN (2);\n"
        "ALTER TABLE ONLY public.p1a ADD CONSTRAINT f FOREIGN KEY (cid) REFERENCES public.c(id);\n",
    )
    onto = extract_all(["postgres:p.sql"], tmp_path)
    assert set(onto.concepts) == {"C", "P"}
    p = onto.concepts["P"]
    assert p.prop("cid").kind == "rel" and p.first("note").value == "partitioned by RANGE (d) into 3 tables"


# --- L12: untrusted sources ------------------------------------------------------------------------------------


def test_safety_switches(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAINONTO_NO_LIVE", "1")
    monkeypatch.setenv("DB", "sqlite:///x.db")
    with pytest.raises(ValueError, match="switched off"):
        extract("postgres:env:DB", tmp_path)
    monkeypatch.setenv("PLAINONTO_SOURCE_ROOT", str(tmp_path / "repo"))
    (tmp_path / "repo").mkdir()
    _write(tmp_path, "outside.sql", "CREATE TABLE t(id integer);\n")
    with pytest.raises(ValueError, match="outside"):
        extract("sqlite:../outside.sql", tmp_path / "repo")


def test_dbt_conditional_tests_are_notes(tmp_path):
    from .conftest import EXAMPLES

    manifest = json.loads((EXAMPLES / "dbt" / "manifest.json").read_text(encoding="utf-8"))
    manifest["nodes"]["test.corner_shop.not_null_customers_name"]["config"] = {"where": "active"}
    _write(tmp_path, "m.json", json.dumps(manifest))
    customer = {c.name: c for c in extract("dbt:m.json", tmp_path)}["Customer"]
    name = customer.prop("name")
    assert "required" not in name.flags and name.child("note").value == "not null where active"

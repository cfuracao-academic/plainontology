"""One test per finding of the independent pre-release review (H = high,
M = medium, L = low), so none of them can come back."""

import asyncio
import json
import sqlite3

import pytest

from plainontology.api import init, refresh
from plainontology.check import validate_record
from plainontology.cli import main
from plainontology.export import to_linkml, to_shacl
from plainontology.extract import extract, extract_all
from plainontology.extract.sqllive import resolve_url
from plainontology.mcp_server import build_server
from plainontology.merge import merge
from plainontology.textformat import dumps, load, loads

from .conftest import EXAMPLES


def _sql(tmp_path, name, ddl):
    (tmp_path / name).write_text(ddl, encoding="utf-8")
    return tmp_path / name


# --- H2: a person's escapes mean what they say and are written back unchanged ---------


def test_h2_unknown_escapes_are_kept():
    text = 'concept A\n  has code : text\n    pattern "^\\d{4}$"\n  note "see C:\\docs"\n'
    onto = loads(text)
    assert onto.concepts["A"].prop("code").children[0].value == "^\\d{4}$"
    assert onto.concepts["A"].first("note").value == "see C:\\docs"
    assert dumps(loads(text)).endswith(text)


# --- H3: an agent's proposal can never make the file unreadable ----------------------


def _propose(path, **args):
    server = build_server(path, allow_proposals=True)
    result = asyncio.run(server.call_tool("propose", args))
    return "\n".join(block.text for block in result.content)


@pytest.mark.parametrize(
    "args,reason",
    [
        ({"concept": "Loan", "line": "min 3"}, "rules go under a property"),
        ({"concept": "Loan", "line": 'note "a\u2028b"'}, "line break"),
        ({"concept": "Loan", "line": 'note "a\rb"'}, "line break"),
        ({"concept": "Loan.member", "line": "has x : text"}, "cannot go under a property"),
        ({"concept": "Loan.nothing", "line": "min 3"}, "not a property"),
    ],
)
def test_h3_bad_proposals_are_refused(examples, args, reason):
    path = examples / "library.onto"
    before = path.read_text(encoding="utf-8")
    assert reason in _propose(path, **args)
    assert path.read_text(encoding="utf-8") == before


def test_h3_good_proposals_keep_the_file_readable(examples):
    path = examples / "library.onto"
    assert _propose(path, concept="Loan", line='synonym "Borrowing"', reason="chat\rlog [x]").startswith("Proposed")
    assert _propose(path, concept="Loan.member", line='pattern "^C-[0-9]+$"').startswith("Proposed")
    onto = load(path)
    loan = onto.concepts["Loan"]
    assert loan.statements[-1].is_proposed and loan.statements[-1].tag.detail == "chat log x"
    assert loan.prop("member").children[-1].is_proposed


# --- H4 and M9: byte order marks ------------------------------------------------------


def test_h4_m9_byte_order_marks(tmp_path):
    dump = _sql(
        tmp_path, "s.sql", "\ufeffCREATE TABLE customers (id integer PRIMARY KEY);\nCREATE TABLE orders (id integer);\n"
    )
    onto, _ = init(tmp_path / "s.onto", [f"postgres:{dump.as_posix()}"])
    assert set(onto.concepts) == {"Customer", "Order"}
    (tmp_path / "bom.onto").write_text("\ufeff" + (tmp_path / "s.onto").read_text(encoding="utf-8"), encoding="utf-8")
    assert set(load(tmp_path / "bom.onto").concepts) == {"Customer", "Order"}
    (tmp_path / "x.json").write_text('\ufeff{"title": "X", "properties": {"a": {"type": "string"}}}', encoding="utf-8")
    assert [c.name for c in extract("jsonschema:x.json", tmp_path)] == ["X"]


def test_h4_empty_source_is_warned(tmp_path):
    _sql(tmp_path, "empty.sql", "\ufeff-- nothing here\n")
    onto = extract_all(["postgres:empty.sql"], tmp_path)
    assert any("produced no concepts" in m for _, m in onto.warnings)


# --- M1: a vanished property holding your lines stays drift until resolved ------------


def test_m1_vanished_property_stays_drift_and_refreshes_when_back(tmp_path):
    db = _sql(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY, isbn TEXT);\n")
    path = tmp_path / "s.onto"
    init(path, [f"sqlite:{db.as_posix()}"])
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "has isbn : text [auto]\n", 'has isbn : text [auto]\n    label "ISBN"\n'
        ),
        encoding="utf-8",
    )
    db.write_text("CREATE TABLE books (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
    assert refresh(path).has_drift
    assert refresh(path).has_drift  # still drift on the next run: CI keeps flagging it
    isbn = load(path).concepts["Book"].prop("isbn")
    assert isbn.is_auto and isbn.children[0].value == "ISBN"
    db.write_text("CREATE TABLE books (id INTEGER PRIMARY KEY, isbn VARCHAR(13) NOT NULL UNIQUE);\n", encoding="utf-8")
    refresh(path)
    isbn = load(path).concepts["Book"].prop("isbn")
    assert isbn.flags == ("required", "unique") and isbn.child("length").value == "..13"
    assert isbn.child("label").value == "ISBN"
    assert not refresh(path).has_drift


# --- M2: your concept header stays yours ----------------------------------------------


def test_m2_header_ownership():
    current = loads('concept Member\n  definition "Mine."\n\nconcept Loan [proposed: agent]\n  note "x"\n')
    fresh = loads(
        "concept Member [auto: sqlite table members]\n  has id : integer [auto]\n\n"
        "concept Loan [auto: sqlite table loans]\n  has id : integer [auto]\n"
    )
    merged, _ = merge(current, fresh)
    assert merged.concepts["Member"].tag is None
    assert merged.concepts["Loan"].tag.kind == "proposed"
    assert merged.concepts["Member"].prop("id").is_auto  # extracted lines still arrive
    text = dumps(merged)
    assert "concept Member\n" in text and "concept Loan [proposed: agent]\n" in text


# --- M3: references to a renamed concept are reported, and rename fixes them -----------


def test_m3_rename_reports_references(tmp_path):
    current = loads(
        "concept Member [auto: sqlite table members]\n  has id : integer key [auto]\n  has name : text [auto]\n"
        '  definition "Mine."\n\nconcept Note\n  about -> Member\n'
    )
    fresh = loads(
        "concept Patron [auto: sqlite table patrons]\n  has id : integer key [auto]\n  has name : text [auto]\n"
    )
    merged, report = merge(current, fresh)
    assert "Patron" in merged.concepts and merged.concepts["Note"].prop("about").target == "Member"
    assert any("still points to Member, now Patron" in c.line for c in report.warnings)
    f = tmp_path / "x.onto"
    f.write_text(dumps(merged), encoding="utf-8")
    assert main(["rename", str(f), "Member", "Patron"]) == 0
    assert load(f).concepts["Note"].prop("about").target == "Patron"


# --- M4: passwords never reach the file ------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://r@db/l?password=x",
        "postgresql://r@db/l?sslpassword=x",
        "mysql://u:p@h/d",
        "postgresql://r@db/l?PWD=x",
    ],
)
def test_m4_passwords_refused(url):
    with pytest.raises(ValueError, match="password"):
        resolve_url(url)


def test_m4_ordinary_query_is_fine():
    assert resolve_url("postgresql://r@db/l?sslmode=require").endswith("?sslmode=require")


# --- M5: a .sql source cannot touch other files ------------------------------------------


@pytest.mark.parametrize(
    "ddl", ["ATTACH DATABASE 'w.db' AS x; CREATE TABLE x.p(a);", "CREATE TABLE t(a); VACUUM INTO 'w.db';"]
)
def test_m5_sql_sources_cannot_write_files(tmp_path, monkeypatch, ddl):
    monkeypatch.chdir(tmp_path)
    _sql(tmp_path, "evil.sql", ddl)
    assert main(["init", "e.onto", "--from", "sqlite:evil.sql"]) == 2
    assert not (tmp_path / "w.db").exists()


# --- M6: failures exit 2, never 1 --------------------------------------------------------


def test_m6_unexpected_errors_exit_2(tmp_path, capsys):
    (tmp_path / "bad.db").write_bytes(b"not a database at all, just text")
    f = tmp_path / "x.onto"
    f.write_text("source sqlite:bad.db\n\nconcept A\n", encoding="utf-8")
    assert main(["drift", str(f)]) == 2
    assert "unexpected error" in capsys.readouterr().err
    (tmp_path / "api.json").write_text(
        json.dumps({"openapi": "3.0.0", "paths": {"/x": {"get": {"responses": {"200": "oops"}}}}}), encoding="utf-8"
    )
    # A malformed part of an OpenAPI file is skipped, not fatal.
    assert main(["init", str(tmp_path / "api.onto"), "--from", f"openapi:{(tmp_path / 'api.json').as_posix()}"]) == 0


# --- M7: drift with no files finds every .onto file ---------------------------------------


def test_m7_drift_without_arguments(examples, monkeypatch, capsys):
    monkeypatch.chdir(examples)
    assert main(["drift"]) == 0
    assert main(["check"]) == 0
    monkeypatch.chdir(examples / "dbt")
    assert main(["drift"]) == 2
    assert "no .onto files" in capsys.readouterr().err


# --- M10: name collisions inside one source --------------------------------------------------


def test_m10_collisions_in_one_source(tmp_path):
    db = sqlite3.connect(tmp_path / "s.db")
    db.executescript(
        "CREATE TABLE book (id integer, title text); CREATE TABLE books (id integer, isbn text);"
        'CREATE TABLE "日本" (id integer); CREATE TABLE "中国" (id integer);'
    )
    db.close()
    onto = extract_all(["sqlite:s.db"], tmp_path)
    assert {"Book", "Books", "日本", "中国"} <= set(onto.concepts)
    assert [s.name for s in onto.concepts["Book"].properties()] == ["id", "title"]
    assert any("would both be concept Book" in m for _, m in onto.warnings)


# --- Low findings ------------------------------------------------------------------------------


def test_l1_key_is_required():
    onto = loads("concept Book\n  has id : integer key\n")
    assert validate_record(onto, "Book", {}) == ["id: required but missing"]


def test_l2_enum_values_with_special_characters():
    onto = loads('concept Shirt\n  has size : enum(S|"M (medium)"|"L|XL")\n')
    assert validate_record(onto, "Shirt", {"size": "M (medium)"}) == []
    assert validate_record(onto, "Shirt", {"size": "L|XL"}) == []
    assert validate_record(onto, "Shirt", {"size": "M"}) != []
    assert '"M (medium)"' in to_shacl(onto) and '"L|XL"' in to_shacl(onto)


def test_l3_linkml_names_do_not_collide():
    import yaml

    onto = loads(
        "concept Book\n  has type_status : enum(a|b)\n\nconcept BookType\n  has status : enum(x|y)\n\n"
        "concept C\n  same_as schema:Thing\n"
    )
    doc = yaml.safe_load(to_linkml(onto, "schema"))
    assert doc["name"] == "schema_schema" and doc["prefixes"]["schema"] == "https://schema.org/"
    ranges = [
        doc["classes"]["Book"]["attributes"]["type_status"]["range"],
        doc["classes"]["BookType"]["attributes"]["status"]["range"],
    ]
    assert len(set(ranges)) == 2
    assert set(doc["enums"][ranges[0]]["permissible_values"]) == {"a", "b"}


def test_l4_dbt_date_ranges(tmp_path):
    manifest = json.loads((EXAMPLES / "dbt" / "manifest.json").read_text(encoding="utf-8"))
    test = manifest["nodes"]["test.corner_shop.accepted_range_orders_amount"]
    test["test_metadata"]["kwargs"] = {
        "column_name": "amount",
        "min_value": "'2020-01-01'",
        "max_value": 100,
        "inclusive": False,
    }
    (tmp_path / "m.json").write_text(json.dumps(manifest), encoding="utf-8")
    order = {c.name: c for c in extract("dbt:m.json", tmp_path)}["Order"]
    notes = [c.value for c in order.prop("amount").children]
    assert "more than '2020-01-01'" in notes and "less than 100" in notes


def test_l6_dates_are_strict():
    onto = loads("concept E\n  has on : date\n  has at : datetime\n")
    assert validate_record(onto, "E", {"on": "2026-11-26", "at": "2026-11-26T10:00:00Z"}) == []
    for bad in ({"on": "20261126"}, {"on": "2026-W48-4"}, {"at": "2026-11-26"}):
        assert validate_record(onto, "E", bad), bad


def test_write_guard_refuses_an_unreadable_file(tmp_path):
    from plainontology.model import Concept, Ontology, Statement
    from plainontology.textformat import save

    onto = Ontology()
    onto.concepts["A"] = Concept("A", statements=[Statement("has", name="9bad", target="text")])
    with pytest.raises(ValueError, match="refusing to write"):
        save(tmp_path / "x.onto", onto)
    assert not (tmp_path / "x.onto").exists()

"""Regressions from the fifth independent review."""

import json

import pytest

from plainontology import api
from plainontology.extract import extract_all
from plainontology.naming import singular
from plainontology.rename import rename
from plainontology.textformat import dumps, load, loads, save

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


def _messages(onto):
    return [m for _c, m in onto.warnings]


def test_renamed_concept_with_the_same_tag_gets_its_new_header(tmp_path):
    schema = {
        "title": "Order",
        "type": "object",
        "properties": {"id": {"type": "integer"}, "total": {"type": "number"}},
    }
    _write(tmp_path, "order.schema.json", json.dumps(schema))
    f = tmp_path / "o.onto"
    api.init(f, [f"jsonschema:{(tmp_path / 'order.schema.json').as_posix()}"])
    text = f.read_text(encoding="utf-8").replace(
        "concept Order [auto: jsonschema order.schema.json]\n",
        'concept Order [auto: jsonschema order.schema.json]\n  synonym "Purchase"\n',
    )
    f.write_text(text, encoding="utf-8")
    _write(tmp_path, "order.schema.json", json.dumps({**schema, "title": "Purchase Order"}))
    assert api.refresh(f).has_drift
    after = f.read_text(encoding="utf-8")
    assert "concept PurchaseOrder [auto: jsonschema order.schema.json]" in after and "concept Order " not in after
    assert 'synonym "Purchase"' in after
    assert not api.compare(f)[2].has_drift  # idempotent


def test_a_persons_line_survives_a_confirmed_proposal_of_the_same_key(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE orders (id INTEGER PRIMARY KEY);\n")
    src = (tmp_path / "s.sql").as_posix()
    f = tmp_path / "o.onto"
    f.write_text(
        f"source sqlite:{src}\n\n"
        "concept Order [auto: sqlite table orders]\n"
        "  has id : integer key [auto]\n"
        "  has currency : text required [proposed: agent]\n\n"
        "concept Purchase\n"
        '  has currency : text "ISO 4217 code, set by finance."\n',
        encoding="utf-8",
    )
    onto = load(f)
    rename(onto, "Purchase", "Order")
    save(f, onto)
    _write(tmp_path, "s.sql", "CREATE TABLE orders (id INTEGER PRIMARY KEY, currency TEXT);\n")
    api.refresh(f)
    after = f.read_text(encoding="utf-8")
    assert 'has currency : text "ISO 4217 code, set by finance."' in after


def test_persons_relation_to_a_renamed_concept_is_reported(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE invoices (id INTEGER PRIMARY KEY, total REAL, due TEXT, paid INTEGER);\n")
    src = (tmp_path / "s.sql").as_posix()
    f = tmp_path / "o.onto"
    api.init(f, [f"sqlite:{src}"])
    f.write_text(f.read_text(encoding="utf-8") + "\nconcept Shipment\n  invoice -> Invoice\n", encoding="utf-8")
    _write(tmp_path, "s.sql", "CREATE TABLE bills (id INTEGER PRIMARY KEY, total REAL, due TEXT, paid INTEGER);\n")
    report = api.compare(f)[2]
    assert any("still points to Invoice, now Bill" in c.line for c in report.warnings)


def test_proposed_concept_produced_by_the_sources_is_reported(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE refunds (id INTEGER PRIMARY KEY);\n")
    f = tmp_path / "o.onto"
    f.write_text(
        f"source sqlite:{(tmp_path / 's.sql').as_posix()}\n\nconcept Refund [proposed: agent]\n", encoding="utf-8"
    )
    report = api.compare(f)[2]
    assert any(c.concept == "Refund" and "still [proposed]" in c.line for c in report.warnings)


def test_concept_rename_is_reported_once(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL);\n")
    f = tmp_path / "o.onto"
    api.init(f, [f"sqlite:{(tmp_path / 's.sql').as_posix()}"])
    f.write_text(
        f.read_text(encoding="utf-8").replace("  has name : text [auto]\n", '  has name : text [auto]\n    note "n"\n'),
        encoding="utf-8",
    )
    _write(tmp_path, "s.sql", "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, price REAL);\n")
    report = api.compare(f)[2]
    assert [c.kind for c in report.changes if c.concept == "Item"] == ["concept_renamed"]


MIGRATION = """
CREATE TYPE order_status AS ENUM ('pending','paid');
CREATE TABLE accounts (id int PRIMARY KEY, email text NOT NULL, status order_status, history order_status[]);
CREATE UNIQUE INDEX accounts_email_uq ON accounts (email);
DROP INDEX accounts_email_uq;
ALTER TYPE order_status ADD VALUE 'refunded';
ALTER TYPE order_status ADD VALUE 'new' BEFORE 'pending';
ALTER TYPE order_status RENAME VALUE 'pending' TO 'open';
ALTER TYPE order_status OWNER TO postgres;
"""


def test_drop_index_and_alter_type_are_applied(tmp_path):
    _write(tmp_path, "m.sql", MIGRATION)
    onto = extract_all(["postgres:m.sql"], tmp_path)
    account = onto.concepts["Account"]
    assert account.prop("email").flags == ("required",)
    assert account.prop("status").target == "enum(new|open|paid|refunded)"
    assert account.prop("history").target == "enum(new|open|paid|refunded)"
    assert _messages(onto) == []
    _write(
        tmp_path,
        "my.sql",
        "CREATE TABLE t (id int PRIMARY KEY, email varchar(50) NOT NULL);\n"
        "CREATE UNIQUE INDEX x ON t (email);\nDROP INDEX x ON t;\n",
    )
    assert extract_all(["mysql:my.sql"], tmp_path).concepts["T"].prop("email").flags == ("required",)


def test_dbt_test_on_an_expression_is_a_note(tmp_path):
    manifest = {
        "sources": {},
        "nodes": {
            "model.p.orders": {
                "resource_type": "model",
                "name": "orders",
                "columns": {"country_code": {"name": "country_code"}, "order_id": {"name": "order_id"}},
            },
            "test.p.u": {
                "resource_type": "test",
                "attached_node": "model.p.orders",
                "column_name": "(country_code || '-' || order_id)",
                "test_metadata": {"name": "unique", "kwargs": {}},
            },
        },
    }
    _write(tmp_path, "manifest.json", json.dumps(manifest))
    order = extract_all(["dbt:manifest.json"], tmp_path).concepts["Order"]
    assert [p.name for p in order.properties()] == ["country_code", "order_id"]
    assert "unique (country_code || '-' || order_id)" in [s.value for s in order.all("note")]


@pytest.mark.parametrize(
    ("plural", "single"),
    [
        ("movies", "movie"),
        ("cookies", "cookie"),
        ("warehouses", "warehouse"),
        ("caches", "cache"),
        ("news", "news"),
        ("aliases", "alias"),
        ("canvas", "canvas"),
        ("quizzes", "quiz"),
        ("categories", "category"),
        ("batches", "batch"),
        ("buses", "bus"),
        ("databases", "database"),
        ("addresses", "address"),
        ("statuses", "status"),
        ("areas", "area"),
    ],
)
def test_singular_names(plural, single):
    assert singular(plural) == single


def test_two_openapi_sources_do_not_warn_about_operation(tmp_path):
    for name in ("a", "b"):
        _write(
            tmp_path,
            f"{name}.json",
            json.dumps(
                {
                    "openapi": "3.0.0",
                    "info": {"title": name, "version": "1"},
                    "paths": {
                        f"/{name}": {"get": {"operationId": f"get_{name}", "responses": {"200": {"description": "ok"}}}}
                    },
                }
            ),
        )
    onto = extract_all(["openapi:a.json", "openapi:b.json"], tmp_path)
    assert not any("Operation" in m for m in _messages(onto)) and not any(c == "Operation" for c, _m in onto.warnings)


def test_length_equality_case_insensitive_regex_unsigned_and_citext(tmp_path):
    _write(
        tmp_path,
        "u.sql",
        "CREATE TABLE t (id int unsigned PRIMARY KEY, n bigint unsigned, sku char(8), CHECK (char_length(sku) = 8));\n",
    )
    _write(
        tmp_path,
        "c.sql",
        "CREATE DOMAIN code AS text CHECK (VALUE ~* '^[a-z]+$');\n"
        "CREATE TABLE c (id int PRIMARY KEY, k code, e public.citext);\n",
    )
    t = extract_all(["mysql:u.sql"], tmp_path).concepts["T"]
    assert t.prop("n").child("min").value == "0" and t.prop("sku").child("length").value == "8..8"
    c = extract_all(["postgres:c.sql"], tmp_path).concepts["C"]
    assert c.prop("k").child("pattern").value == "(?i)^[a-z]+$" and c.prop("e").target == "text"


def test_people_keep_their_indentation_when_the_file_is_written(tmp_path):
    from plainontology.model import AUTO, Statement

    text = 'concept A\n    has x : text\n        note "n"\n    has y : text\n'
    onto = loads(text)
    assert dumps(onto) == text
    onto.concepts["A"].statements[0].children.append(Statement("min", value="1", tag=AUTO))
    onto.concepts["A"].statements.append(Statement("synonym", value="S"))
    out = dumps(onto)
    assert out.startswith('concept A\n    has x : text\n        note "n"\n')
    again = loads(out)  # still reads back with the same structure
    assert again.concepts["A"].prop("x").child("min") is not None and again.concepts["A"].prop("y") is not None

"""Regressions found on real schemas and in the third review."""

import pytest

pytest.importorskip("sqlglot")

from plainontology.extract.sqldump import tables_from_ddl

BASE = "CREATE TABLE actor (actor_id INT PRIMARY KEY, first_name VARCHAR(45));\n"


@pytest.mark.parametrize(
    "header",
    [
        "CREATE DEFINER=CURRENT_USER SQL SECURITY INVOKER VIEW",
        "CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`localhost` SQL SECURITY DEFINER VIEW",
        "CREATE OR REPLACE DEFINER='app'@'%' VIEW",
    ],
)
def test_mysql_view_modifiers_are_read(header):
    warnings: list[str] = []
    sql = BASE + f"{header} actor_info AS SELECT a.actor_id, a.first_name FROM actor a;\n"
    tables = {t.name: t for t in tables_from_ddl(sql, "mysql", warnings)}
    assert "actor_info" in tables and tables["actor_info"].kind == "view"
    assert [c.name for c in tables["actor_info"].columns] == ["actor_id", "first_name"]
    assert warnings == []


def test_postgres_materialized_view_with_no_data_is_read():
    warnings: list[str] = []
    sql = BASE + "CREATE MATERIALIZED VIEW public.names AS\n SELECT a.first_name FROM actor a\n  WITH NO DATA;\n"
    tables = {t.name: t for t in tables_from_ddl(sql, "postgres", warnings)}
    assert "names" in tables and tables["names"].kind == "view"
    assert warnings == []


def test_unreadable_create_view_is_a_warning_not_silence():
    warnings: list[str] = []
    sql = BASE + "CREATE VIEW v AS SELECT FROM WHERE ))) nonsense;\n"
    tables_from_ddl(sql, "postgres", warnings)
    assert any("could not read" in w and "CREATE VIEW v" in w for w in warnings), warnings


def test_functions_and_procedures_are_not_flagged():
    warnings: list[str] = []
    sql = BASE + (
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN CREATE TABLE x (a int); RETURN 1; END $$ LANGUAGE plpgsql;\n"
    )
    tables_from_ddl(sql, "postgres", warnings)
    assert not any("could not read" in w for w in warnings), warnings


# --- third review: nothing extracted is dropped silently ------------------------------------

import json  # noqa: E402

from plainontology.extract import extract_all  # noqa: E402
from plainontology.textformat import dumps  # noqa: E402


def _messages(onto):
    return [m for _c, m in onto.warnings]


def test_pydantic_root_ref_follows_the_definition(tmp_path):
    schema = {
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "value": {"type": "integer"},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                },
                "required": ["value"],
            }
        },
        "$ref": "#/$defs/Node",
    }
    for stem in ("node", "tree"):
        (tmp_path / f"{stem}.json").write_text(json.dumps(schema), encoding="utf-8")
        onto = extract_all([f"jsonschema:{stem}.json"], tmp_path)
        assert list(onto.concepts) == ["Node"]
        assert onto.concepts["Node"].prop("value") is not None


def test_root_title_like_a_definition_keeps_both(tmp_path):
    schema = {
        "title": "Order",
        "type": "object",
        "properties": {"order": {"$ref": "#/$defs/Order"}},
        "$defs": {"Order": {"type": "object", "properties": {"total": {"type": "number"}}}},
    }
    (tmp_path / "o.json").write_text(json.dumps(schema), encoding="utf-8")
    onto = extract_all(["jsonschema:o.json"], tmp_path)
    assert onto.concepts["Order"].prop("total") is not None
    assert onto.concepts["OrderDocument"].prop("order") is not None
    assert any("OrderDocument" in m for m in _messages(onto))


def test_required_applies_to_properties_inside_allof(tmp_path):
    schema = {
        "allOf": [{"properties": {"id": {"type": "integer"}, "total": {"type": "number"}}}],
        "required": ["id", "total"],
    }
    (tmp_path / "r.json").write_text(json.dumps(schema), encoding="utf-8")
    concept = extract_all(["jsonschema:r.json"], tmp_path).concepts["R"]
    assert "required" in concept.prop("id").flags and "required" in concept.prop("total").flags


JOIN = """
CREATE TABLE people (id INTEGER PRIMARY KEY);
CREATE TABLE books (id INTEGER PRIMARY KEY, authors TEXT);
CREATE TABLE book_authors (book_id INTEGER REFERENCES books(id), author_id INTEGER REFERENCES people(id));
CREATE TABLE book_editors (book_id INTEGER REFERENCES books(id), person_id INTEGER REFERENCES people(id));
CREATE TABLE book_reviewers (book_id INTEGER REFERENCES books(id), person_id INTEGER REFERENCES people(id));
CREATE TABLE book_tags (book_id INTEGER REFERENCES books(id), tag_id INTEGER REFERENCES tags(id));
"""


@pytest.mark.parametrize("kind", ["sqlite", "postgres"])
def test_join_tables_are_never_dropped(tmp_path, kind):
    (tmp_path / "j.sql").write_text(JOIN, encoding="utf-8")
    onto = extract_all([f"{kind}:j.sql"], tmp_path)
    book = onto.concepts["Book"]
    assert book.prop("authors").kind == "has"
    assert book.prop("book_authors").target == "Person"
    assert book.prop("persons").target == "Person"
    assert book.prop("book_reviewers").target == "Person"
    assert "BookTag" in onto.concepts  # links to a table not in the file: kept as a concept
    assert sum("join table" in m for m in _messages(onto)) == 2


def test_tools_with_clashing_names_are_kept(tmp_path):
    tools = [
        {"name": "get_book", "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
        {"name": "getBook", "input_schema": {"type": "object", "properties": {"isbn": {"type": "string"}}}},
        {"name": "tool", "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}}},
    ]
    (tmp_path / "t.json").write_text(json.dumps(tools), encoding="utf-8")
    onto = extract_all(["tools:t.json"], tmp_path)
    assert onto.concepts["GetBook"].prop("id") is not None
    assert onto.concepts["GetBook2"].prop("isbn") is not None
    assert onto.concepts["Tool2"].prop("x") is not None
    assert len(_messages(onto)) == 2


def test_openapi_parameters_and_body_with_one_name_are_kept(tmp_path):
    body = {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}, "title": {}}}
    returned = {"type": "object", "properties": {"a": {}}}
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/books/{id}": {
                "put": {
                    "operationId": "updateBook",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {"name": "id", "in": "header", "schema": {"type": "string"}},
                        {"name": "returns", "in": "query", "schema": {"type": "string"}},
                    ],
                    "requestBody": {"content": {"application/json": {"schema": body}}},
                    "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": returned}}}},
                }
            }
        },
    }
    (tmp_path / "a.json").write_text(json.dumps(doc), encoding="utf-8")
    onto = extract_all(["openapi:a.json"], tmp_path)
    names = [p.name for p in onto.concepts["UpdateBook"].properties()]
    assert names == ["id", "header_id", "returns", "body_id", "title", "response"]
    assert len(_messages(onto)) == 3


def _dbt(tmp_path, nodes):
    (tmp_path / "manifest.json").write_text(json.dumps({"nodes": nodes, "sources": {}}), encoding="utf-8")
    return extract_all(["dbt:manifest.json"], tmp_path)


def test_dbt_relation_beside_a_column_of_its_name(tmp_path):
    columns = {"customer": {"name": "customer", "data_type": "varchar"}, "customer_id": {"name": "customer_id"}}
    onto = _dbt(
        tmp_path,
        {
            "model.p.orders": {"resource_type": "model", "name": "orders", "columns": columns},
            "model.p.customers": {"resource_type": "model", "name": "customers", "columns": {}},
            "test.p.rel": {
                "resource_type": "test",
                "attached_node": "model.p.orders",
                "column_name": "customer_id",
                "test_metadata": {"name": "relationships", "kwargs": {"to": "ref('customers')", "field": "id"}},
            },
        },
    )
    order = onto.concepts["Order"]
    assert order.prop("customer").kind == "has"
    assert order.prop("customer_id").target == "Customer"
    assert _messages(onto)


def test_dbt_infinite_bound_is_ignored(tmp_path):
    (tmp_path / "manifest.json").write_text(
        '{"sources": {}, "nodes": {"model.p.t": {"resource_type": "model", "name": "t",'
        ' "columns": {"n": {"name": "n"}}}, "test.p.r": {"resource_type": "test", "attached_node": "model.p.t",'
        ' "column_name": "n", "test_metadata": {"name": "accepted_range", "kwargs": {"min_value": 1e400}}}}}',
        encoding="utf-8",
    )
    onto = extract_all(["dbt:manifest.json"], tmp_path)
    assert onto.concepts["T"].prop("n").child("min") is None


def test_dbt_project_folder_stays_inside_the_source_root(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text('{"nodes": {}, "sources": {}}', encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "proj").mkdir(parents=True)
    try:
        (repo / "proj" / "target").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("no permission to make links here")
    monkeypatch.setenv("PLAINONTO_SOURCE_ROOT", str(repo))
    with pytest.raises(ValueError, match="outside"):
        extract_all(["dbt:proj"], repo)


def test_postgres_like_create_as_and_inherits(tmp_path):
    (tmp_path / "i.sql").write_text(
        "CREATE TABLE base (id int PRIMARY KEY, code text NOT NULL CHECK (char_length(code) <= 5));\n"
        "CREATE TABLE copy1 (LIKE base INCLUDING ALL);\n"
        "CREATE TABLE copy2 (LIKE base, extra int);\n"
        "CREATE TABLE copy3 AS SELECT * FROM base;\n"
        "CREATE TABLE child (extra text) INHERITS (base);\n",
        encoding="utf-8",
    )
    onto = extract_all(["postgres:i.sql"], tmp_path)
    c = onto.concepts
    assert c["Copy1"].prop("id").flags == ("key",) and c["Copy1"].prop("code").child("length") is not None
    assert c["Copy2"].prop("id").flags == ("required",) and c["Copy2"].prop("code").child("length") is None
    assert c["Copy3"].prop("code") is not None and "created from a query" in dumps(onto)
    assert c["Child"].first("is_a").name == "Base" and c["Child"].prop("code") is None
    assert _messages(onto) == []


def test_migrations_keep_checks_with_their_columns(tmp_path):
    (tmp_path / "m.sql").write_text(
        "CREATE TABLE members (id int PRIMARY KEY);\n"
        "CREATE TABLE loans (\n"
        "  id int PRIMARY KEY,\n"
        "  status text NOT NULL CHECK (status IN ('open','closed')),\n"
        "  days int CHECK (days > 0),\n"
        "  code text CONSTRAINT code_ok CHECK (code ~ '^[A-Z]+$'),\n"
        "  member_id int CONSTRAINT loans_member_fk REFERENCES members(id),\n"
        "  price numeric,\n"
        "  CHECK (price > 0 AND price < 100)\n"
        ");\n"
        "ALTER TABLE loans RENAME COLUMN status TO state;\n"
        "ALTER TABLE loans DROP CONSTRAINT loans_days_check;\n"
        "ALTER TABLE loans DROP CONSTRAINT code_ok;\n"
        "ALTER TABLE loans DROP CONSTRAINT loans_member_fk;\n"
        "ALTER TABLE loans DROP COLUMN price;\n",
        encoding="utf-8",
    )
    onto = extract_all(["postgres:m.sql"], tmp_path)
    loan = onto.concepts["Loan"]
    assert loan.prop("state").target == "enum(open|closed)"
    assert loan.prop("days").children == [] and loan.prop("code").children == []
    assert loan.prop("member_id").kind == "has"
    assert "price" not in dumps(onto) and _messages(onto) == []


def test_mysql_default_check_names_can_be_dropped(tmp_path):
    (tmp_path / "m.sql").write_text(
        "CREATE TABLE t (id int PRIMARY KEY, n int CHECK (n > 0), m int CHECK (m < 9));\n"
        "ALTER TABLE t DROP CHECK t_chk_2;\n",
        encoding="utf-8",
    )
    t = extract_all(["mysql:m.sql"], tmp_path).concepts["T"]
    assert t.prop("n").child("min") is not None and t.prop("m").children == []


def test_domain_checks_and_not_null_apply_to_the_column(tmp_path):
    (tmp_path / "d.sql").write_text(
        "CREATE DOMAIN positive_int AS integer CHECK (VALUE > 0);\n"
        "CREATE DOMAIN short_code AS text NOT NULL CHECK (char_length(VALUE) >= 2);\n"
        "CREATE DOMAIN size AS text CHECK (VALUE IN ('S','M,L','XL'));\n"
        "CREATE TABLE t (id positive_int PRIMARY KEY, code short_code, s size);\n",
        encoding="utf-8",
    )
    t = extract_all(["postgres:d.sql"], tmp_path).concepts["T"]
    assert t.prop("id").child("min").value == "1"
    assert t.prop("code").flags == ("required",) and t.prop("code").child("length") is not None
    assert t.prop("s").target == "enum(S|M,L|XL)"

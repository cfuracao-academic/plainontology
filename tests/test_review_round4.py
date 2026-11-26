"""Regressions from the fourth independent review."""

import json

import pytest

from plainontology import api
from plainontology.check import validate_record
from plainontology.extract import extract_all
from plainontology.rename import rename
from plainontology.textformat import dump_concept, dumps, load, loads, save

pytest.importorskip("sqlglot")


def _messages(onto):
    return [m for _c, m in onto.warnings]


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


# --- H1: a person's lines survive a proposal and an extracted line of one key ------------------


def test_rename_merge_then_refresh_keeps_the_proposal_note(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE patrons (id INTEGER PRIMARY KEY, nickname TEXT);\n")
    src = (tmp_path / "s.sql").as_posix()
    f = tmp_path / "lib.onto"
    f.write_text(
        f"source sqlite:{src}\n\n"
        "concept Member [auto: sqlite table members]\n"
        "  has nickname : text [proposed]\n"
        '    note "what friends call them"\n\n'
        "concept Patron [auto: sqlite table patrons]\n"
        "  has id : integer key [auto]\n"
        "  has nickname : text [auto]\n",
        encoding="utf-8",
    )
    onto = load(f)
    rename(onto, "Member", "Patron")
    save(f, onto)
    api.refresh(f)
    _write(tmp_path, "s.sql", "CREATE TABLE patrons (id INTEGER PRIMARY KEY, nickname TEXT, joined DATE);\n")
    api.refresh(f)
    assert "what friends call them" in f.read_text(encoding="utf-8")


def test_confirmed_proposal_keeps_notes_under_the_extracted_line(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE patrons (id INTEGER PRIMARY KEY, nickname TEXT);\n")
    src = (tmp_path / "s.sql").as_posix()
    f = tmp_path / "lib.onto"
    f.write_text(
        f"source sqlite:{src}\n\n"
        "concept Patron [auto: sqlite table patrons]\n"
        "  has id : integer key [auto]\n"
        "  has nickname : text [proposed]\n"
        "  has nickname : text [auto]\n"
        '    note "a person wrote this"\n',
        encoding="utf-8",
    )
    api.refresh(f)
    after = f.read_text(encoding="utf-8")
    assert "a person wrote this" in after and "[proposed]" not in after


# --- H2, H3, M2, M3, L5: JSON Schema and OpenAPI ---------------------------------------------


def test_inline_object_never_takes_a_definitions_name(tmp_path):
    schema = {
        "title": "Order",
        "type": "object",
        "properties": {
            "item": {"type": "object", "properties": {"sku": {"type": "string"}}},
            "line": {"$ref": "#/$defs/OrderItem"},
        },
        "$defs": {"OrderItem": {"type": "object", "properties": {"qty": {"type": "integer"}}}},
    }
    _write(tmp_path, "order.json", json.dumps(schema))
    onto = extract_all(["jsonschema:order.json"], tmp_path)
    assert onto.concepts["OrderItem"].prop("qty") is not None
    assert onto.concepts["Order"].prop("line").target == "OrderItem"
    item = onto.concepts["Order"].prop("item").target
    assert item != "OrderItem" and onto.concepts[item].prop("sku") is not None
    assert _messages(onto)


API = """\
openapi: 3.0.0
info: {title: t, version: "1"}
paths:
  /pets:
    post:
      operationId: addPet
      requestBody:
        content:
          multipart/form-data:
            schema: {type: object, properties: {photo: {type: string, format: binary}, name: {type: string}}}
      responses:
        "200": {description: ok, content: {application/json: {schema: {$ref: "#/components/schemas/Pet"}}}}
  /notes:
    put:
      operationId: putNote
      requestBody:
        content:
          text/plain: {}
      responses: {"204": {description: done}}
components:
  schemas:
    Pet:
      oneOf: [{$ref: "#/components/schemas/Cat"}, {$ref: "#/components/schemas/Dog"}]
    Cat: {type: object, properties: {purrs: {type: boolean}}}
    Dog: {type: object, properties: {barks: {type: boolean}}}
    Owner:
      type: object
      properties:
        pets: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        vet: {$ref: "./schemas/vet.yaml#/Vet"}
"""


def test_openapi_unions_form_bodies_and_yaml_references(tmp_path):
    pytest.importorskip("yaml")
    _write(tmp_path, "api.yaml", API)
    (tmp_path / "schemas").mkdir()
    _write(tmp_path, "schemas/vet.yaml", "Vet:\n  type: object\n  properties:\n    name: {type: string}\n")
    onto = extract_all(["openapi:api.yaml"], tmp_path)
    c = onto.concepts
    assert "Pet" in c and c["Cat"].first("is_a").name == "Pet" and c["Dog"].first("is_a").name == "Pet"
    assert c["Owner"].prop("pets").target == "Pet" and c["AddPet"].prop("returns").target == "Pet"
    assert c["AddPet"].prop("photo").target == "binary" and c["AddPet"].prop("name") is not None
    assert c["Vet"].prop("name") is not None
    assert any("PUT /notes" in m and "no schema" in m for m in _messages(onto))


def test_bad_yaml_reference_names_the_file(tmp_path):
    pytest.importorskip("yaml")
    _write(tmp_path, "api.yaml", API)
    (tmp_path / "schemas").mkdir()
    _write(tmp_path, "schemas/vet.yaml", "Vet: [unclosed\n")
    with pytest.raises(ValueError, match=r"vet\.yaml"):
        extract_all(["openapi:api.yaml"], tmp_path)


def test_named_union_in_defs(tmp_path):
    schema = {
        "type": "object",
        "properties": {"pet": {"$ref": "#/$defs/Pet"}, "id": {"$ref": "#/$defs/Id"}},
        "$defs": {
            "Pet": {"anyOf": [{"$ref": "#/$defs/Cat"}, {"$ref": "#/$defs/Dog"}]},
            "Cat": {"type": "object", "properties": {"purrs": {"type": "boolean"}}},
            "Dog": {"type": "object", "properties": {"barks": {"type": "boolean"}}},
            "Id": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        },
    }
    _write(tmp_path, "home.json", json.dumps(schema))
    onto = extract_all(["jsonschema:home.json"], tmp_path)
    home = onto.concepts["Home"]
    assert home.prop("pet").target == "Pet" and "Pet" in onto.concepts
    assert home.prop("id").kind == "has" and "Id" not in onto.concepts


def test_document_that_is_a_list(tmp_path):
    schema = {
        "title": "Readings",
        "type": "array",
        "items": {"type": "object", "properties": {"t": {"type": "string"}, "site": {"type": "string"}}},
    }
    _write(tmp_path, "list.json", json.dumps(schema))
    readings = extract_all(["jsonschema:list.json"], tmp_path).concepts["Readings"]
    assert readings.prop("t") is not None and readings.prop("site") is not None


def test_boolean_minimum_is_not_a_bound(tmp_path):
    schema = {"type": "object", "properties": {"price": {"type": "number", "minimum": True}}}
    _write(tmp_path, "p.json", json.dumps(schema))
    price = extract_all(["jsonschema:p.json"], tmp_path).concepts["P"].prop("price")
    assert price.child("min") is None and "not a usable number" in price.child("note").value


# --- M6: validate takes a record's own field names ------------------------------------------


def test_validate_accepts_the_original_field_names(tmp_path):
    schema = {
        "type": "object",
        "properties": {"first name": {"type": "string"}, "@id": {"type": "string"}},
        "required": ["first name", "@id"],
    }
    _write(tmp_path, "person.json", json.dumps(schema))
    onto = extract_all(["jsonschema:person.json"], tmp_path)
    assert validate_record(onto, "Person", {"first name": "Ann", "@id": "https://x/1"}) == []
    assert validate_record(onto, "Person", {"first_name": "Ann", "_id": "https://x/1"}) == []


# --- M1, M4, M5, M5b, L1, L3: SQL --------------------------------------------------------------


def test_mysql_introducers_and_postgres_e_strings(tmp_path):
    bs = "\\"
    _write(
        tmp_path,
        "m.sql",
        f"CREATE TABLE u (id int PRIMARY KEY, code varchar(9), "
        f"CONSTRAINT c1 CHECK (regexp_like(`code`,_utf8mb4'^[A-Z]{{2}}{bs}{bs}d+$')));\n",
    )
    _write(tmp_path, "p.sql", f"CREATE TABLE p (id int PRIMARY KEY, code text CHECK (code ~ E'^{bs}{bs}d{{2}}$'));\n")
    my = extract_all(["mysql:m.sql"], tmp_path).concepts["U"].prop("code")
    pg = extract_all(["postgres:p.sql"], tmp_path).concepts["P"].prop("code")
    assert my.child("pattern").value == "(?i)^[A-Z]{2}" + bs + "d+$"
    assert pg.child("pattern").value == "^" + bs + "d{2}$"


FK = """
CREATE TABLE authors (id integer PRIMARY KEY);
CREATE TABLE users (id integer PRIMARY KEY);
CREATE TABLE regions (region text, num int, PRIMARY KEY (region, num));
CREATE TABLE books (id integer PRIMARY KEY, author_id integer REFERENCES authors(id) CHECK (author_id > 0),
  code text, CHECK (char_length(code) >= 5), CHECK (char_length(code) >= 2),
  n bigint CHECK (n >= 9007199254740993) CHECK (n >= 1));
CREATE TABLE profiles (user_id integer PRIMARY KEY REFERENCES users(id), bio text);
CREATE TABLE passports (id integer PRIMARY KEY, user_id integer UNIQUE REFERENCES users(id));
CREATE TABLE lines (region text, num int, pos int, PRIMARY KEY (region, num, pos),
  FOREIGN KEY (region, num) REFERENCES regions(region, num));
CREATE TABLE a_b (a_id integer PRIMARY KEY REFERENCES authors(id), b_id integer REFERENCES users(id));
"""


def test_foreign_key_columns_keep_their_details(tmp_path):
    from plainontology.check import check

    _write(tmp_path, "fk.sql", FK)
    onto = extract_all(["postgres:fk.sql"], tmp_path)
    c = onto.concepts
    assert "check: author_id > 0" in [s.value for s in c["Book"].all("note")]
    assert c["Book"].prop("code").child("length").value == "5.."
    assert c["Book"].prop("n").child("min").value == "9007199254740993"
    assert "unique" in c["Profile"].prop("user").flags and "unique" in c["Passport"].prop("user").flags
    assert c["Line"].first("unique").name == "region, pos"
    assert "AB" in c and c["AB"].prop("b").flags == ()  # keyed by one link only: not a join table
    assert [i for i in check(onto) if i.level == "error"] == []


def test_table_linking_to_a_join_table_is_kept(tmp_path):
    _write(
        tmp_path,
        "jt.sql",
        "CREATE TABLE books (id integer PRIMARY KEY);\nCREATE TABLE tags (id integer PRIMARY KEY);\n"
        "CREATE TABLE book_tags (book_id integer REFERENCES books(id), tag_id integer REFERENCES tags(id),"
        " PRIMARY KEY (book_id, tag_id));\n"
        "CREATE TABLE x (bt integer REFERENCES book_tags(book_id), note text);\n",
    )
    onto = extract_all(["postgres:jt.sql"], tmp_path)
    assert "X" in onto.concepts and onto.concepts["X"].prop("bt").target in onto.concepts


def test_postgres_unquoted_names_fold_to_lower_case(tmp_path):
    _write(
        tmp_path,
        "case.sql",
        "CREATE TABLE Authors (Id integer PRIMARY KEY, Name text);\n"
        'CREATE TABLE "Quoted" (id integer PRIMARY KEY);\n'
        "CREATE TABLE books (id integer PRIMARY KEY, author_id integer REFERENCES AUTHORS(ID),"
        ' q integer REFERENCES "Quoted"(id));\n'
        "ALTER TABLE authors ADD COLUMN bio text;\n"
        "COMMENT ON COLUMN AUTHORS.NAME IS 'Full name.';\n"
        "ALTER TABLE BOOKS ALTER COLUMN AUTHOR_ID SET NOT NULL;\n",
    )
    onto = extract_all(["postgres:case.sql"], tmp_path)
    assert sorted(onto.concepts) == ["Author", "Book", "Quoted"]
    author = onto.concepts["Author"]
    assert author.prop("name").value == "Full name." and author.prop("bio") is not None
    assert onto.concepts["Book"].prop("author").flags == ("required",)
    assert _messages(onto) == []


@pytest.mark.parametrize("kind", ["sqlite", "postgres"])
def test_partial_and_expression_unique_indexes_are_notes(tmp_path, kind):
    _write(
        tmp_path,
        "u.sql",
        "CREATE TABLE users (id integer PRIMARY KEY, email text, handle text, deleted_at text);\n"
        "CREATE UNIQUE INDEX u1 ON users (email) WHERE deleted_at IS NULL;\n"
        "CREATE UNIQUE INDEX u2 ON users (lower(handle));\n",
    )
    user = extract_all([f"{kind}:u.sql"], tmp_path).concepts["User"]
    assert "unique" not in user.prop("email").flags and "unique" not in user.prop("handle").flags
    notes = " | ".join(s.value for s in user.all("note")).lower()
    assert "unique (email) where deleted_at is null" in notes and "lower(handle)" in notes


# --- L2, L4, L6, L7 -------------------------------------------------------------------------------


def test_a_changed_concept_tag_is_drift_and_written(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY);\n")
    f = tmp_path / "b.onto"
    api.init(f, [f"sqlite:{(tmp_path / 's.sql').as_posix()}"])
    _write(tmp_path, "s.sql", "CREATE TABLE book (id INTEGER PRIMARY KEY);\n")
    report = api.refresh(f)
    assert report.has_drift and "[auto: sqlite table book]" in f.read_text(encoding="utf-8")


def test_shacl_subclass_redefining_a_property():
    rdflib = pytest.importorskip("rdflib")
    pyshacl = pytest.importorskip("pyshacl")
    from plainontology.export import to_shacl

    onto = loads(
        "prefix : <https://ex.org/>\n"
        "concept Animal\n  has id : integer key\n  has name : text required\n"
        "concept Dog\n  is_a Animal\n  has id : text key\n"
    )
    shapes = rdflib.Graph().parse(data=to_shacl(onto), format="turtle")
    data = (
        "@prefix : <https://ex.org/> .\n"
        ':d1 a :Dog ; <https://ex.org/Dog/id> "T-1" ; <https://ex.org/Animal/name> "Rex" .\n'
        ':d2 a :Dog ; <https://ex.org/Dog/id> "T-2" .\n'
    )
    _ok, graph, _text = pyshacl.validate(rdflib.Graph().parse(data=data, format="turtle"), shacl_graph=shapes)
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    focus = {str(o) for o in graph.objects(None, sh.focusNode)}
    assert focus == {"https://ex.org/d2"}  # only the missing inherited name


def test_bom_and_leading_blank_lines_are_kept(tmp_path):
    f = tmp_path / "x.onto"
    text = "\n\n# header\nconcept A\n  has x : text\n"
    f.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    onto = load(f)
    onto.concepts["A"].statements.append(loads('concept B\n  note "n"\n').concepts["B"].statements[0])
    save(f, onto)
    assert f.read_bytes() == b"\xef\xbb\xbf" + (text + '  note "n"\n').encode("utf-8")
    assert dumps(loads(text)) == text and dump_concept(onto.concepts["A"])[0] == "concept A"


def test_live_connections_have_a_timeout(monkeypatch):
    sa = pytest.importorskip("sqlalchemy")
    from plainontology.extract import sqllive

    seen = {}

    def fake_engine(url, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(sa, "create_engine", fake_engine)
    with pytest.raises(RuntimeError):
        sqllive.tables_from_database("postgresql+psycopg://u@db.invalid/x")
    assert seen["connect_args"] == {"connect_timeout": sqllive.CONNECT_SECONDS}

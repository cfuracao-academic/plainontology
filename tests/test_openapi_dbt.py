"""OpenAPI and dbt sources."""

import json
import shutil

import pytest

from plainontology.extract import extract

from .conftest import EXAMPLES


def _lines(concepts):
    out = {}
    for c in concepts:
        lines = []
        for s in c.statements:
            lines.append(s.body())
            lines += ["  " + ch.body() for ch in s.children]
        out[c.name] = lines
    return out


def test_openapi_yaml():
    concepts = _lines(extract("openapi:library.openapi.yaml", EXAMPLES))
    assert list(concepts) == ["Book", "Author", "Loan", "Operation", "ListBooks", "LendBook"]
    assert concepts["Book"][:3] == [
        'definition "A title the library holds; one record per edition."',
        "has id : integer required",
        "has title : text required",
    ]
    assert concepts["ListBooks"] == [
        "is_a Operation",
        'definition "List books in the catalogue, optionally filtered by author."',
        'note "GET /books"',
        'has author : text "Part of the author\'s name."',
        "has limit : integer",
        "  min 1",
        "  max 100",
        'returns -> Book many "The matching books."',
    ]
    lend = concepts["LendBook"]
    assert "has bookId : integer required" in lend  # path parameter declared on the path
    assert lend[lend.index('has member : text required "Library card number."') + 1] == '  pattern "^C-[0-9]+$"'
    assert 'returns -> Loan "The new loan."' in lend


def test_openapi_tags_name_the_source():
    concepts = {c.name: c for c in extract("openapi:library.openapi.yaml", EXAMPLES)}
    assert concepts["Book"].tag.detail == "openapi library.openapi.yaml #/components/schemas/Book"


def test_swagger_2(tmp_path):
    spec = {
        "swagger": "2.0",
        "info": {"title": "Pets", "version": "1"},
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "addPet",
                    "parameters": [
                        {"in": "body", "name": "pet", "required": True, "schema": {"$ref": "#/definitions/Pet"}}
                    ],
                    "responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/Pet"}}},
                }
            }
        },
        "definitions": {"Pet": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    }
    (tmp_path / "pets.json").write_text(json.dumps(spec), encoding="utf-8")
    concepts = _lines(extract("openapi:pets.json", tmp_path))
    assert concepts["Pet"] == ["has name : text required"]
    assert "body -> Pet required" in concepts["AddPet"] and 'returns -> Pet "ok"' in concepts["AddPet"]


def test_not_openapi(tmp_path):
    (tmp_path / "x.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="not an OpenAPI"):
        extract("openapi:x.json", tmp_path)


def test_dbt_manifest():
    concepts = _lines(extract("dbt:dbt/manifest.json", EXAMPLES))
    assert list(concepts) == ["StgOrder", "Customer", "Order", "TillOrder", "TillCustomer"]
    order = concepts["Order"]
    assert "has order_id : integer required unique" in order  # not_null + unique tests
    assert 'customer -> Customer "Who placed the order."' in order  # relationships test
    assert order[order.index('has amount : decimal "Order value in pounds."') + 1] == "  min 0"  # accepted_range
    assert "has status : enum(placed|shipped|returned)" in order  # accepted_values
    assert 'note "built from: StgOrder"' in order
    assert 'note "measure order_total: sum of amount (Sum of order values.)"' in order  # semantic model
    assert "has customer_id : integer key" in concepts["Customer"]  # primary_key constraint
    assert concepts["TillOrder"] == ['definition "Raw orders exported nightly from the till."']


def test_dbt_project_folder(tmp_path):
    (tmp_path / "shop" / "target").mkdir(parents=True)
    shutil.copy(EXAMPLES / "dbt" / "manifest.json", tmp_path / "shop" / "target" / "manifest.json")
    assert "Order" in {c.name for c in extract("dbt:shop", tmp_path)}

import json
import sqlite3

import pytest

from plainontology.extract import extract, parse_source
from plainontology.extract.sqlite import datatype
from plainontology.model import Ontology
from plainontology.naming import concept_name, relation_name

from .conftest import EXAMPLES


def _onto(concepts):
    onto = Ontology()
    for c in concepts:
        onto.add_concept(c)
    return onto


def _lines(concept):
    return [s.body() for s in concept.statements]


def test_naming():
    assert concept_name("authors") == "Author"
    assert concept_name("book_tags") == "BookTag"
    assert concept_name("categories") == "Category"
    assert concept_name("status") == "Status"
    assert concept_name("addresses") == "Address"
    assert relation_name("author_id") == "author"
    assert relation_name("parentId") == "parent"


@pytest.mark.parametrize("declared,expected", [
    ("INTEGER", "integer"), ("VARCHAR(13)", "text"), ("TIMESTAMP", "datetime"), ("DATE", "date"),
    ("BOOLEAN", "boolean"), ("NUMERIC(10,2)", "decimal"), ("BLOB", "binary"), ("", "any"), ("JSON", "json"),
])
def test_sqlite_datatypes(declared, expected):
    assert datatype(declared) == expected


def test_sqlite_schema_file():
    onto = _onto(extract("sqlite:library.sql", EXAMPLES))
    assert list(onto.concepts) == ["Author", "Book", "Tag", "Loan"]  # book_tags is a join table
    book = _lines(onto.concepts["Book"])
    assert "has id : integer key" in book
    assert "has title : text required" in book
    assert "has isbn : text unique" in book
    assert "author -> Author required" in book
    assert "tags -> Tag many" in book
    assert "has label : text required unique" in _lines(onto.concepts["Tag"])


def test_sqlite_database_file_matches_schema_file(tmp_path):
    db = tmp_path / "library.db"
    con = sqlite3.connect(db)
    con.executescript((EXAMPLES / "library.sql").read_text(encoding="utf-8"))
    con.close()
    from_db = _onto(extract(f"sqlite:{db}", tmp_path))
    from_sql = _onto(extract("sqlite:library.sql", EXAMPLES))
    assert {n: _lines(c) for n, c in from_db.concepts.items()} == {n: _lines(c) for n, c in from_sql.concepts.items()}


def test_json_schema():
    onto = _onto(extract("jsonschema:article.schema.json", EXAMPLES))
    article = _lines(onto.concepts["Article"])
    assert 'definition "A published piece of writing in a magazine issue."' in article
    assert "has status : enum(draft|in review|live) required" in article
    assert "has word_count : integer" in article  # ["integer", "null"]
    assert "has published : datetime" in article
    assert "authors -> Person many" in article
    assert "issue -> ArticleIssue" in article
    assert "has keywords : text many" in article
    assert "has volume : integer required" in _lines(onto.concepts["ArticleIssue"])
    assert _lines(onto.concepts["Editor"]) == ["is_a Person", "has desk : text"]


@pytest.mark.parametrize("shape", ["anthropic", "openai_chat", "openai_responses", "mcp"])
def test_tool_formats(tmp_path, shape):
    schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    tool = {
        "anthropic": {"name": "get_weather", "description": "Weather now.", "input_schema": schema},
        "openai_chat": {"type": "function", "function": {"name": "get_weather", "description": "Weather now.", "parameters": schema}},
        "openai_responses": {"type": "function", "name": "get_weather", "description": "Weather now.", "parameters": schema},
        "mcp": {"name": "get_weather", "description": "Weather now.", "inputSchema": schema},
    }[shape]
    doc = {"tools": [tool]} if shape == "mcp" else [tool]
    (tmp_path / "tools.json").write_text(json.dumps(doc), encoding="utf-8")
    onto = _onto(extract("tools:tools.json", tmp_path))
    assert _lines(onto.concepts["GetWeather"]) == ["is_a Tool", 'definition "Weather now."', "has city : text required"]


def test_bad_sources():
    with pytest.raises(ValueError):
        parse_source("mysql:x")
    with pytest.raises(ValueError):
        parse_source("library.sql")

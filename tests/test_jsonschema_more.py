"""JSON Schema: references to other files, unions and constraints."""

import json

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


def test_article_example():
    concepts = _lines(extract("jsonschema:article.schema.json", EXAMPLES))
    article = concepts["Article"]
    assert article[article.index('has headline : text required "Title shown on the card."') + 1] == "  length ..120"
    assert article[article.index("has word_count : integer") + 1] == "  min 0"
    assert "  format uri" in article
    assert 'reviewer -> Reviewer "Who checked the article."' in article  # $ref to another file
    assert "attachment -> ArticleAttachment" in article  # oneOf of two objects
    assert 'has rating : any "one of: integer, text"' in article  # anyOf of simple types
    assert concepts["Reviewer"][:2] == [
        'definition "A person who checks an article before it goes live; defined in its own file."',
        "has name : text required",
    ]
    assert concepts["ArticleAttachment"] == ['definition "One of: Image, Video."']
    assert "is_a ArticleAttachment" in concepts["Image"] and "is_a ArticleAttachment" in concepts["Video"]
    video = concepts["Video"]
    assert video[video.index("has seconds : integer") + 1] == "  min 1"  # exclusiveMinimum 0 on an integer


def test_enum_options_merge(tmp_path):
    schema = {
        "title": "Ticket",
        "type": "object",
        "properties": {
            "state": {"oneOf": [{"enum": ["open", "closed"]}, {"const": "archived"}]},
            "price": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
            "owner": {"$ref": "https://example.org/person.json"},
        },
    }
    (tmp_path / "ticket.json").write_text(json.dumps(schema), encoding="utf-8")
    ticket = _lines(extract("jsonschema:ticket.json", tmp_path))["Ticket"]
    assert "has state : enum(open|closed|archived)" in ticket
    assert ticket[ticket.index("has price : decimal") + 1 :][:2] == ["  max 100", '  note "greater than 0"']
    assert '  note "1 to 5 items"' in ticket
    assert "owner -> Person" in ticket  # web references are not fetched; check flags the target

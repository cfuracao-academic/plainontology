"""Format 0.2: lines under properties, language tags, unique sets and rules."""

import pytest

from plainontology.textformat import ParseError, dumps, loads, parse_statement

TEXT = """\
concept Book
  label "Book"
  label "Livre"@fr
  synonym "Libro"@es
  has isbn : text unique "ISBN-13."
    label "ISBN"
    synonym "Numéro ISBN"@fr
    length 13..13
    pattern "^[0-9]{13}$"
  has pages : integer
    min 1 [auto]
    max 5000
  has email : text
    format email
  author -> Author required
    note "The first author when there are several."
  unique (isbn, pages)
"""


def test_round_trip():
    assert dumps(loads(TEXT)).endswith(TEXT)


def test_children_attach_to_their_property():
    book = loads(TEXT).concepts["Book"]
    isbn = book.prop("isbn")
    assert [c.kind for c in isbn.children] == ["label", "synonym", "length", "pattern"]
    assert isbn.children[1].lang == "fr"
    assert book.prop("pages").child("min").is_auto
    assert book.prop("author").children[0].kind == "note"
    assert [s.kind for s in book.statements] == ["label", "label", "synonym", "has", "has", "has", "rel", "unique"]


def test_language_tags_give_separate_keys():
    book = loads(TEXT).concepts["Book"]
    labels = book.all("label")
    assert {s.lang for s in labels} == {"", "fr"}
    assert labels[0].key() != labels[1].key()


def test_unique_key_ignores_order():
    assert parse_statement("unique (a, b)").key() == parse_statement("unique (b,a)").key()


@pytest.mark.parametrize(
    "text,message",
    [
        ("concept A\n  min 1\n", "belongs under a property"),
        ("concept A\n  has x : text\n    has y : text\n", "cannot sit under a property"),
        ("concept A\n  has x : text\n    length ..\n", "at least one bound"),
        ('concept A\n  has x : text\n    pattern "(["\n', "not a valid regular expression"),
    ],
)
def test_errors(text, message):
    with pytest.raises(ParseError, match=message):
        loads(text)


def test_child_after_non_property_is_an_error():
    with pytest.raises(ParseError):
        loads('concept A\n  label "x"\n    min 1\n')

import pytest

from plainontology.textformat import ParseError, dumps, load, loads, parse_statement

from .conftest import EXAMPLES


def test_example_round_trips_byte_for_byte():
    text = (EXAMPLES / "library.onto").read_text(encoding="utf-8")
    assert dumps(loads(text)) == text


def test_statement_kinds():
    s = parse_statement('has isbn : text unique required "ISBN-13, no \\"hyphens\\"." [auto: x]')
    assert (s.kind, s.name, s.target, s.flags) == ("has", "isbn", "text", ("unique", "required"))
    assert s.value == 'ISBN-13, no "hyphens".'
    assert s.tag.kind == "auto" and s.tag.detail == "x"

    r = parse_statement("author -> Author required [auto: fk books.author_id]")
    assert (r.kind, r.name, r.target, r.flags) == ("rel", "author", "Author", ("required",))

    assert parse_statement("same_as schema:Person").name == "schema:Person"
    assert parse_statement("is_a Tool [auto]").tag.kind == "auto"
    assert parse_statement('synonym "Title"').value == "Title"
    assert parse_statement("has status : enum(draft|in review|live)").target == "enum(draft|in review|live)"
    assert parse_statement('note "x" [proposed: agent]').is_proposed


def test_tag_text_inside_quotes_is_not_a_tag():
    s = parse_statement('label "ends with [auto]"')
    assert s.tag is None and s.value == "ends with [auto]"


def test_bad_line_reports_line_number():
    with pytest.raises(ParseError) as err:
        loads("concept A\n  has x : text\n  this is not valid\n")
    assert err.value.line_no == 3


def test_structure_errors():
    with pytest.raises(ParseError):
        loads("  has x : text\n")
    with pytest.raises(ParseError):
        loads("concept A\nconcept A\n")
    with pytest.raises(ParseError):
        loads("concept A\nsource sqlite:x.db\n")


def test_comments_are_kept_in_place():
    text = "# head\n\nconcept A\n  # inside\n  has x : text\n\n# before B\nconcept B\n\n# tail\n"
    onto = loads(text)
    assert onto.comments == [" head"]
    assert onto.concepts["B"].leading_comments == [" before B"]
    assert onto.trailing_comments == [" tail"]
    assert loads(dumps(onto)).concepts["A"].statements[0].kind == "comment"


def test_load_example():
    onto = load(EXAMPLES / "library.onto")
    assert {"Author", "Book", "Tag", "Loan", "Tool", "SearchCatalogue", "ReserveBook"} <= set(onto.concepts)
    assert onto.prefixes[""] == "https://example.org/library#"

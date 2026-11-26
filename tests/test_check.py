from plainontology.check import check, validate_record
from plainontology.textformat import load, loads

from .conftest import EXAMPLES


def test_example_is_clean():
    assert check(load(EXAMPLES / "library.onto")) == []


def test_check_finds_problems():
    onto = loads(
        "concept A\n"
        "  has x : text\n"
        "  has x : integer\n"
        "  has y : colour\n"
        "  b -> Missing\n"
        "  same_as foo:Bar\n"
        '  synonym "Aye" [proposed: agent]\n'
    )
    levels = sorted(i.level for i in check(onto))
    assert levels == ["error", "info", "warning", "warning", "warning"]


def test_validate_record():
    onto = load(EXAMPLES / "library.onto")
    good = {"id": 1, "title": "Dune", "isbn": "9780441013593", "author": {"id": 7, "name": "Frank Herbert"}, "tags": [3, 4]}
    assert validate_record(onto, "Book", good) == []
    bad = {"title": 5, "colour": "red", "author": {"born": "not a date"}, "tags": 3, "published": "2026-13-01"}
    issues = validate_record(onto, "book", bad)
    assert "title: expected text, got int 5" in issues
    assert "colour: not a property of Book" in issues
    assert "author.name: required but missing" in issues
    assert any(i.startswith("author.born: expected date") for i in issues)
    assert "tags: expected a list" in issues
    assert any(i.startswith("published: expected date") for i in issues)


def test_validate_inherited_and_enum():
    onto = load(EXAMPLES / "library.onto")
    assert validate_record(onto, "ReserveBook", {"book_id": 1, "member": "C-12", "pickup": "desk"}) == []
    assert validate_record(onto, "ReserveBook", {"book_id": 1, "member": "C-12", "pickup": "drone"}) == [
        "pickup: 'drone' is not one of desk, locker"
    ]
    assert validate_record(onto, "Nope", {}) == ["unknown concept 'Nope'"]

"""Rules under properties: checked by `check`, enforced by `validate`, kept by refresh."""

from plainontology.check import check, validate_record
from plainontology.merge import merge
from plainontology.textformat import loads

ONTO = loads("""\
concept Reader
  has card : text required
    length 4..10
    pattern "^C-[0-9]+$"
  has email : text
    format email
  has age : integer
    min 12
    max 120
  has site : text
    format uri
  favourite -> Item

concept Item
  definition "One of: Book, Film."

concept Book
  is_a Item
  has isbn : text required

concept Film
  is_a Item
  has minutes : integer required
""")


def test_valid_record():
    record = {
        "card": "C-123",
        "email": "a@example.org",
        "age": 30,
        "site": "https://example.org",
        "favourite": {"minutes": 120},
    }
    assert validate_record(ONTO, "Reader", record) == []


def test_rule_violations():
    issues = validate_record(ONTO, "Reader", {"card": "X", "email": "nope", "age": 7, "site": "example"})
    assert "card: length 1 is outside 4..10" in issues
    assert "card: 'X' does not match the pattern ^C-[0-9]+$" in issues
    assert "email: 'nope' is not a valid email" in issues
    assert "age: 7 is below the minimum 12" in issues
    assert "site: 'example' is not a valid uri" in issues


def test_nested_object_may_match_any_subtype():
    assert validate_record(ONTO, "Reader", {"card": "C-1234", "favourite": {"isbn": "9780441013593"}}) == []
    issues = validate_record(ONTO, "Reader", {"card": "C-1234", "favourite": {"colour": "red"}})
    assert issues and all(i.startswith("favourite.") for i in issues)


def test_check_flags_misplaced_rules():
    onto = loads(
        'concept A\n  has n : text\n    min 1\n  has m : integer\n    min 5\n    max 1\n  b -> A\n    pattern "x"\n'
    )
    messages = [i.message for i in check(onto)]
    assert any("needs a numeric property" in m for m in messages)
    assert any("min 5 is greater than max 1" in m for m in messages)
    assert any("apply to values, not to relations" in m for m in messages)


def test_refresh_keeps_your_rules_and_updates_extracted_ones():
    current = loads('concept A [auto: x]\n  has code : text [auto]\n    length ..8 [auto]\n    pattern "^[A-Z]+$"\n')
    fresh = loads("concept A [auto: x]\n  has code : text [auto]\n    length ..12 [auto]\n")
    merged, report = merge(current, fresh)
    children = [c.render() for c in merged.concepts["A"].prop("code").children]
    assert children == ["length ..12 [auto]", 'pattern "^[A-Z]+$"']
    assert [c.render() for c in report.changes] == ["~ A.code: length ..12  (was: length ..8)"]

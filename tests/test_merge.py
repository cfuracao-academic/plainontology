from plainontology.merge import merge
from plainontology.textformat import dumps, loads

CURRENT = """\
concept Book [auto: sqlite table books]
  has id : integer key [auto]
  has title : text [auto]
  has isbn : text "ISBN-13."
  definition "A title we hold."
  same_as schema:Book [proposed: agent]

concept Shelf [auto: sqlite table shelves]
  has id : integer key [auto]

concept Room [auto: sqlite table rooms]
  has id : integer key [auto]
  note "Rooms are going away."

concept Idea
  definition "Only in the person's head."
"""

FRESH = """\
concept Book [auto: sqlite table books]
  has id : integer key [auto]
  has title : text required [auto]
  has isbn : text unique [auto]
  has pages : integer [auto]

concept Member [auto: sqlite table members]
  has id : integer key [auto]
"""


def _merge():
    return merge(loads(CURRENT), loads(FRESH))


def test_people_lines_are_kept_and_auto_lines_refreshed():
    merged, _ = _merge()
    book = merged.concepts["Book"]
    bodies = [s.render() for s in book.statements]
    assert bodies == [
        "has id : integer key [auto]",
        "has title : text required [auto]",
        'has isbn : text "ISBN-13."',
        'definition "A title we hold."',
        "same_as schema:Book [proposed: agent]",
        "has pages : integer [auto]",
    ]


def test_report_lists_every_kind_of_change():
    _, report = _merge()
    kinds = {(c.kind, c.concept) for c in report.changes}
    assert ("changed", "Book") in kinds
    assert ("added", "Book") in kinds
    assert ("kept_yours", "Book") in kinds
    assert ("concept_removed", "Shelf") in kinds
    assert ("concept_kept", "Room") in kinds
    assert ("concept_added", "Member") in kinds
    assert report.has_drift


def test_concepts_gone_from_sources():
    merged, _ = _merge()
    assert "Shelf" not in merged.concepts  # extracted, nothing of yours
    room = merged.concepts["Room"]  # extracted, but holds your note
    assert room.tag is None and [s.kind for s in room.statements] == ["note"]
    assert "Idea" in merged.concepts  # yours entirely


def test_merge_is_idempotent():
    merged, _ = _merge()
    again, report = merge(merged, loads(FRESH))
    assert dumps(again) == dumps(merged)
    assert not report.has_drift


def test_inputs_are_not_modified():
    current, fresh = loads(CURRENT), loads(FRESH)
    before = dumps(current), dumps(fresh)
    merge(current, fresh)
    assert (dumps(current), dumps(fresh)) == before

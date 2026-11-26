"""Renames: detected on refresh, or done by hand with `plainonto rename`."""

from plainontology.cli import main
from plainontology.merge import merge
from plainontology.rename import rename
from plainontology.textformat import dumps, load, loads

CURRENT = """\
concept Book [auto: sqlite table books]
  has id : integer key [auto]
  has title : text required [auto]
    label "Title"@en
  has isbn : text [auto]
  has pages : integer [auto]
  definition "A title the library holds."

concept Loan [auto: sqlite table loans]
  has id : integer key [auto]
  book -> Book required [auto: fk loans.book_id]
"""


def test_property_kept_when_it_holds_your_lines():
    fresh = loads(
        "concept Book [auto: sqlite table books]\n"
        "  has id : integer key [auto]\n  has isbn : text [auto]\n  has pages : integer [auto]\n"
    )
    merged, report = merge(loads(CURRENT), fresh)
    title = merged.concepts["Book"].prop("title")
    assert title.tag is None and [c.render() for c in title.children] == ['label "Title"@en']
    assert any(c.kind == "property_kept" for c in report.changes)


def test_property_rename_moves_your_lines():
    fresh = loads(
        CURRENT.replace(
            'has title : text required [auto]\n    label "Title"@en\n', "has headline : text required [auto]\n"
        )
    )
    merged, report = merge(loads(CURRENT), fresh)
    book = merged.concepts["Book"]
    assert book.prop("title") is None
    assert [c.render() for c in book.prop("headline").children] == ['label "Title"@en']
    assert any(c.kind == "property_renamed" and c.previous == "title" for c in report.changes)


def test_concept_rename_moves_your_lines():
    fresh = loads(
        CURRENT.replace("concept Book [auto: sqlite table books]", "concept Volume [auto: sqlite table volumes]")
        .replace('    label "Title"@en\n', "")
        .replace('  definition "A title the library holds."\n', "")
        .replace("book -> Book", "volume -> Volume")
    )
    merged, report = merge(loads(CURRENT), fresh)
    assert list(merged.concepts) == ["Volume", "Loan"]  # same place in the file
    volume = merged.concepts["Volume"]
    assert volume.definition() == "A title the library holds."
    assert [c.render() for c in volume.prop("title").children] == ['label "Title"@en']
    assert any(c.kind == "concept_renamed" and c.previous == "Book" for c in report.changes)
    again, report2 = merge(merged, fresh)
    assert dumps(again) == dumps(merged) and not report2.has_drift


def test_unrelated_concept_is_not_taken_for_a_rename():
    fresh = loads(
        "concept Member [auto: sqlite table members]\n  has id : integer key [auto]\n  has email : text [auto]\n"
        "\nconcept Loan [auto: sqlite table loans]\n  has id : integer key [auto]\n"
    )
    merged, report = merge(loads(CURRENT), fresh)
    assert "Book" in merged.concepts and "Member" in merged.concepts
    assert any(c.kind == "concept_kept" for c in report.changes)


def test_rename_command(tmp_path, capsys):
    f = tmp_path / "lib.onto"
    f.write_text(
        CURRENT + "\nconcept Shelf\n  is_a Book\n  unique (code, room)\n  has code : text\n  has room : text\n",
        encoding="utf-8",
    )
    assert main(["rename", str(f), "Book", "Volume"]) == 0
    onto = load(f)
    assert "Volume" in onto.concepts and "Book" not in onto.concepts
    assert onto.concepts["Loan"].prop("book").target == "Volume"
    assert onto.concepts["Shelf"].first("is_a").name == "Volume"
    assert main(["rename", str(f), "Shelf.code", "Shelf.shelf_code"]) == 0
    shelf = load(f).concepts["Shelf"]
    assert shelf.prop("shelf_code") is not None and shelf.first("unique").name == "shelf_code, room"
    assert "Renamed" in capsys.readouterr().out


def test_rename_onto_existing_concept_merges():
    onto = loads(
        'concept Old\n  definition "Kept."\n  synonym "Legacy"\n\nconcept New [auto: x]\n  has id : integer [auto]\n'
    )
    message = rename(onto, "Old", "New")
    assert message.startswith("Merged Old into New")
    assert list(onto.concepts) == ["New"] and onto.concepts["New"].definition() == "Kept."

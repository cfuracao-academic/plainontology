"""A file goes back to disk exactly as a person wrote it, apart from the
extracted lines a refresh changes."""

import asyncio

from plainontology.api import init, refresh
from plainontology.check import check
from plainontology.mcp_server import build_server
from plainontology.textformat import dumps, load, loads

PERSONAL = """\
# my own header


prefix   ex:   <https://ex.org/>
prefix ex: <https://other.example/>
source   sqlite:s.sql

concept Book [auto: sqlite table books]

  has id : integer key [auto]
# a note at the start of the line, inside Book
  has title : text [auto]
  definition   "Spaced   on purpose"



concept Shelf
  note "kept"

# trailing one

# trailing two
"""


def test_personal_layout_round_trips_byte_for_byte():
    assert dumps(loads(PERSONAL)) == PERSONAL


def test_column_zero_comment_stays_inside_its_concept():
    onto = loads(PERSONAL)
    book = onto.concepts["Book"]
    assert book.statements[1].kind == "comment" and book.statements[1].col0
    assert onto.concepts["Shelf"].leading_comments == []


def test_duplicate_prefix_is_flagged():
    messages = [i.message for i in check(loads(PERSONAL))]
    assert any("prefix ex is declared more than once" in m for m in messages)


def test_crlf_and_no_header_are_kept(tmp_path):
    text = "concept A\r\n  has x : text\r\n"
    f = tmp_path / "a.onto"
    f.write_bytes(text.encode("utf-8"))
    onto = load(f)
    assert onto.newline == "\r\n"
    from plainontology.textformat import save

    save(f, onto)
    assert f.read_bytes() == text.encode("utf-8")


def test_refresh_and_propose_keep_the_layout(tmp_path):
    (tmp_path / "s.sql").write_text("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);\n", encoding="utf-8")
    f = tmp_path / "lib.onto"
    f.write_text(PERSONAL, encoding="utf-8")
    (tmp_path / "s.sql").write_text(
        "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, pages INTEGER);\n", encoding="utf-8"
    )
    assert refresh(f).has_drift
    after = f.read_text(encoding="utf-8")
    expected = PERSONAL.replace(
        '  definition   "Spaced   on purpose"\n', '  definition   "Spaced   on purpose"\n  has pages : integer [auto]\n'
    )
    assert after == expected
    server = build_server(f, allow_proposals=True)
    asyncio.run(server.call_tool("propose", {"concept": "Shelf", "line": 'synonym "Rack"'}))
    after = f.read_text(encoding="utf-8")
    assert after == expected.replace('  note "kept"\n', '  note "kept"\n  synonym "Rack" [proposed: agent]\n')


def test_blank_line_above_an_extracted_line_survives_refresh(tmp_path):
    (tmp_path / "s.sql").write_text("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);\n", encoding="utf-8")
    f = tmp_path / "lib.onto"
    init(f, [f"sqlite:{(tmp_path / 's.sql').as_posix()}"])
    text = f.read_text(encoding="utf-8").replace("  has title : text [auto]\n", "\n  has title : text [auto]\n")
    f.write_text(text, encoding="utf-8")
    (tmp_path / "s.sql").write_text(
        "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL);\n", encoding="utf-8"
    )
    refresh(f)
    assert "\n\n  has title : text required [auto]\n" in f.read_text(encoding="utf-8")

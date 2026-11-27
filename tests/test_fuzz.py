"""Randomised checks that run on every push (fixed seeds, so a failure is repeatable).

They guard the promises that review rounds kept finding broken:

* writing a file never moves a line, comments included, and a second write changes nothing;
* refresh, with people's edits and comments and the sources changing, never refuses to
  write, never loses a comment and settles (the next run reports no new drift).

Set PLAINONTO_FUZZ_SCALE (default 5; for example 50) to run more cases locally.
"""

import os
import random

import pytest

from plainontology import api
from plainontology.textformat import dumps, load, loads

SCALE = int(os.environ.get("PLAINONTO_FUZZ_SCALE", "5"))  # 5 catches the regressions of review rounds 7 and 8

LINES = [
    ("  has {n} : text", 1),
    ("  has {n} : integer [auto]", 1),
    ("    has {n} : integer", 1),
    ("\thas {n} : text", 1),
    ('  note "n{n}"', 0),
    ('  synonym "s{n}" [proposed]', 0),
    ("  is_a P{n}", 0),
    ('    note "child {n}"', 2),
    ("      min 1 [auto]", 2),
    ("  # indented {n}", 3),
    ("    # deep {n}", 3),
    ("\t# tab {n}", 3),
    ("# col0 {n}", 3),
    ("", 3),
]


def _placed(onto):
    """Every line with the concept or property it sits in, comments included, in order."""
    out = []
    for c in onto.concepts.values():
        out.append(("lead", c.name, tuple(x.strip() for x in c.leading_comments if x.strip())))
        for s in c.statements:
            out.append((c.name, s.kind, s.name, s.value))
            out.extend((c.name, s.name, ch.kind, ch.name, ch.value) for ch in s.children)
    out.append(("trail", tuple(x.strip() for x in onto.trailing_comments if x.strip())))
    return out


def _random_file(rng):
    out = []
    if rng.random() < 0.3:
        out += ["source sqlite:x.sql", "# a header comment"] + ([""] if rng.random() < 0.5 else [])
    for c in range(rng.randint(1, 3)):
        if rng.random() < 0.3:
            out.append(f"# about C{c}")
            if rng.random() < 0.3:
                out.append("")
        out.append(f"concept C{c}" + (" [auto: x]" if rng.random() < 0.5 else ""))
        last_prop = False
        for i in range(rng.randint(0, 9)):
            text, kind = rng.choice(LINES)
            if kind == 2 and not last_prop:
                continue
            out.append(text.format(n=f"{c}_{i}"))
            last_prop = True if kind == 1 else (False if kind == 0 else last_prop)
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("seed", range(4))
def test_writing_never_moves_a_line_and_settles(seed):
    rng = random.Random(seed)
    for _ in range(300 * SCALE):
        try:
            first = loads(_random_file(rng))
        except ValueError:
            continue  # a random file that is not valid in the first place
        once = dumps(first)
        second = loads(once)
        assert _placed(second) == _placed(first), once
        assert dumps(second) == once


TABLES = {
    "authors": ["name TEXT", "born INTEGER", "bio TEXT"],
    "books": ["title TEXT", "pages INTEGER", "isbn TEXT", "year INTEGER"],
    "tags": ["label TEXT", "colour TEXT"],
    "loans": ["due TEXT", "days INTEGER"],
}
BANNERS = ["# ===== {t} =====", "# {t}: see the wiki"]
EXTRA = [
    '    note "child"',
    "    # child comment",
    "# col0 comment",
    "  # concept comment",
    '  synonym "S"',
    "  has mine : text",
]
BY_DESIGN = ("kept_yours", "warning", "property_kept", "concept_kept")


def _schema(rng):
    out = []
    for table, cols in TABLES.items():
        if rng.random() < 0.7:
            picked = rng.sample(cols, rng.randint(1, len(cols)))
            out.append(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, {', '.join(picked)});")
    return "\n".join(out or ["CREATE TABLE books (id INTEGER PRIMARY KEY);"]) + "\n"


@pytest.mark.parametrize("seed", range(3))
def test_refresh_with_people_editing_never_refuses_loses_or_keeps_drifting(tmp_path, seed):
    rng = random.Random(seed)
    for case in range(12 * SCALE):
        folder = tmp_path / f"c{case}"
        folder.mkdir()
        sql, f = folder / "s.sql", folder / "lib.onto"
        sql.write_text(_schema(rng), encoding="utf-8")
        api.init(f, [f"sqlite:{sql.as_posix()}"])
        edited = []
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("concept ") and rng.random() < 0.6:
                edited.append(rng.choice(BANNERS).format(t=line.split()[1]))
                if rng.random() < 0.5:
                    edited.append("")
            edited.append(line)
            if line.startswith("  ") and rng.random() < 0.4:
                edited.append(rng.choice(EXTRA))
        f.write_text("\n".join(edited) + "\n", encoding="utf-8")
        mine = [x.strip() for x in edited if x.strip().startswith("#") and "PlainOntology" not in x]
        mine = [x for x in mine if not x.startswith(("# Lines ending", "# Every other"))]
        for _cycle in range(3):
            sql.write_text(_schema(rng), encoding="utf-8")
            api.refresh(f)  # raises if a write would move a line
            again = api.compare(f)[2]
            assert not [c for c in again.changes if c.kind not in BY_DESIGN], again.render()
        text = f.read_text(encoding="utf-8")
        assert all(m in text for m in mine), text
        load(f)

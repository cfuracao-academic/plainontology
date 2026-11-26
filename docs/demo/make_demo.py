"""Render docs/demo.gif from real PlainOntology commands.

Every command in the GIF is run for real in a temporary folder and its actual
output is drawn, so the demo cannot drift from what the tool does.

    pip install pillow
    python docs/demo/make_demo.py            # from the repo root

Set PLAINONTO_DEMO_FONT to a monospace .ttf if none of the defaults is found.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "demo.gif"

W, H = 960, 620
PAD_X, TOP, LINE_H, FONT_SIZE = 26, 58, 24, 17
ROWS = (H - TOP - 14) // LINE_H

BG = (22, 24, 31)
BAR = (36, 39, 49)
FG = (222, 225, 230)
DIM = (118, 124, 140)
PROMPT = (98, 200, 140)
STRING = (232, 192, 112)
KEYWORD = (112, 172, 255)
ADDED = (120, 220, 150)
REMOVED = (240, 112, 112)
YOURS_BG = (30, 54, 42)
YOURS_MARK = (120, 220, 150)

FONTS = [
    os.environ.get("PLAINONTO_DEMO_FONT", ""),
    "CascadiaMono.ttf", "DejaVuSansMono.ttf", "consola.ttf", "Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = FONTS
    if bold:
        names = [n.replace("consola.ttf", "consolab.ttf").replace("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf") for n in FONTS] + FONTS
    for name in names:
        if not name:
            continue
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    sys.exit("No monospace font found; set PLAINONTO_DEMO_FONT to a .ttf file")


FONT = load_font(FONT_SIZE)
TITLE_FONT = load_font(40, bold=True)
SUB_FONT = load_font(20)

Segment = tuple[str, tuple[int, int, int]]


class Line:
    def __init__(self, segments: list[Segment], yours: bool = False):
        self.segments = segments
        self.yours = yours


# --- colouring ----------------------------------------------------------------

_TAG = re.compile(r"(\s\[(?:auto|proposed)[^\]]*\])$")
_STR = re.compile(r'("(?:[^"\\]|\\.)*")')


def onto_line(text: str) -> Line:
    """Colour one line of an .onto file; untagged statements are 'yours'."""
    if text.startswith("concept "):
        m = _TAG.search(text)
        body, tag = (text[: m.start()], m.group(1)) if m else (text, "")
        return Line([("concept ", KEYWORD), (body[8:], FG), (tag, DIM)])
    m = _TAG.search(text)
    body, tag = (text[: m.start()], m.group(1)) if m else (text, "")
    segments: list[Segment] = []
    for i, part in enumerate(_STR.split(body)):
        if part:
            segments.append((part, STRING if i % 2 else FG))
    if tag:
        segments.append((tag, DIM))
    yours = bool(text.strip()) and not tag and text.startswith("  ")
    return Line(segments, yours=yours)


def output_line(text: str) -> Line:
    colour = {"+": ADDED, "-": REMOVED, "~": STRING, "=": KEYWORD, "!": STRING}.get(text[:1], FG)
    return Line([(text, colour)])


# --- screen -------------------------------------------------------------------


class Screen:
    def __init__(self):
        self.lines: list[Line] = []
        self.frames: list[Image.Image] = []
        self.durations: list[int] = []

    def render(self) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 38], fill=BAR)
        for i, colour in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
            d.ellipse([18 + i * 22, 13, 30 + i * 22, 25], fill=colour)
        d.text((W // 2, 19), "plainonto", font=FONT, fill=DIM, anchor="mm")
        for row, line in enumerate(self.lines[-ROWS:]):
            y = TOP + row * LINE_H
            if line.yours:
                d.rectangle([PAD_X - 10, y - 3, W - PAD_X + 6, y + LINE_H - 5], fill=YOURS_BG)
                d.rectangle([PAD_X - 10, y - 3, PAD_X - 7, y + LINE_H - 5], fill=YOURS_MARK)
            x = PAD_X
            for text, colour in line.segments:
                d.text((x, y), text, font=FONT, fill=colour)
                x += d.textlength(text, font=FONT)
        return img

    def snap(self, ms: int) -> None:
        self.frames.append(self.render())
        self.durations.append(ms)

    def hold(self, ms: int) -> None:
        self.durations[-1] += ms

    def clear(self) -> None:
        self.lines = []

    def type(self, command: str, step: int = 3) -> None:
        line = Line([("$ ", PROMPT), ("", FG)])
        self.lines.append(line)
        colour = DIM if command.startswith("#") else FG
        for i in range(0, len(command) + 1, step):
            line.segments[1] = (command[:i], colour)
            self.snap(40)
        line.segments[1] = (command, colour)
        self.snap(400)

    def show(self, lines: list[Line], hold: int) -> None:
        self.lines.extend(lines)
        self.snap(hold)

    def card(self, title: str, subtitle: str, footer: str, hold: int) -> None:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.text((W // 2, H // 2 - 50), title, font=TITLE_FONT, fill=FG, anchor="mm")
        d.text((W // 2, H // 2 + 8), subtitle, font=SUB_FONT, fill=PROMPT, anchor="mm")
        d.text((W // 2, H // 2 + 60), footer, font=FONT, fill=DIM, anchor="mm")
        self.frames.append(img)
        self.durations.append(hold)


# --- the demo -----------------------------------------------------------------


def run(work: Path, *args: str) -> tuple[list[str], int]:
    proc = subprocess.run(
        [sys.executable, "-m", "plainontology", *args],
        cwd=work, capture_output=True, text=True, encoding="utf-8",
    )
    text = (proc.stdout + proc.stderr).rstrip("\n")
    return text.split("\n"), proc.returncode


def block(work: Path, name: str) -> list[str]:
    """What `sed -n '/concept NAME/,/^$/p' library.onto` prints, minus the blank line."""
    lines = (work / "library.onto").read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith(f"concept {name}"))
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="plainonto-demo-"))
    try:
        shutil.copy(ROOT / "examples" / "library.postgres.sql", work / "library.postgres.sql")
        s = Screen()
        show_book = "sed -n '/concept Book/,/^$/p' library.onto"

        s.card("PlainOntology", "your system's ontology, as one plain text file",
               "PostgreSQL, MySQL, SQLite, JSON Schema, AI agent tools", 2600)

        s.type("plainonto init library.onto --from postgres:library.postgres.sql")
        out, _ = run(work, "init", "library.onto", "--from", "postgres:library.postgres.sql")
        s.show([output_line(l) for l in out], 900)
        s.type(show_book)
        s.show([onto_line(l) for l in block(work, "Book")], 2600)
        s.type("# comments, CHECKs and lengths came from the database")
        s.hold(1800)

        # A person adds meaning to the extracted structure.
        path = work / "library.onto"
        text = path.read_text(encoding="utf-8")
        isbn = '  has isbn : text unique "ISBN-13 without hyphens." [auto]\n    length ..13 [auto]\n'
        tags = "  tags -> Tag many [auto: via book_tags]\n"
        assert isbn in text and tags in text
        text = text.replace(isbn, isbn + '    label "ISBN"\n')
        text = text.replace(tags, tags + '  label "Livre"@fr\n  synonym "Title"\n  same_as schema:Book\n')
        path.write_text(text, encoding="utf-8", newline="\n")
        s.clear()
        s.type("# add meaning in any editor: untagged lines are yours")
        s.type(show_book)
        s.show([onto_line(l) for l in block(work, "Book")], 3200)

        # The system changes underneath.
        s.clear()
        s.type("# the database changes")
        change = "ALTER TABLE books ADD edition int CHECK (edition > 0);"
        sql = work / "library.postgres.sql"
        sql.write_text(sql.read_text(encoding="utf-8") + change + "\n", encoding="utf-8", newline="\n")
        s.type(f'echo "{change}" >> library.postgres.sql')
        s.type("plainonto drift library.onto")
        out, code = run(work, "drift", "library.onto")
        s.show([output_line(l) for l in out], 1300)
        s.type(f"# exit code {code}: a CI check fails until you refresh")
        s.hold(1200)
        s.type("plainonto refresh library.onto")
        out, _ = run(work, "refresh", "library.onto")
        s.show([output_line(l) for l in out], 1500)

        s.clear()
        s.type(show_book)
        s.show([onto_line(l) for l in block(work, "Book")], 3400)
        s.type("# your lines kept, the new column added with its rule")
        s.hold(2000)

        s.card("Try it: pip install plainontology", "plainonto init my.onto --from sqlite:my.db",
               "github.com/cfuracao-academic/plainontology", 4500)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    palette = s.frames[-6].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in s.frames]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=s.durations, loop=0, optimize=True, disposal=1)
    total = sum(s.durations) / 1000
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(frames)} frames, {total:.0f} s, {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()

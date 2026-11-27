"""Read and write the PlainOntology plain text format. See SPEC.md."""

from __future__ import annotations

import re
from pathlib import Path

from .model import CHILD_KINDS, FLAGS, RULE_KINDS, Concept, Ontology, Statement, Tag, unquote

FORMAT_VERSION = "0.2"

DEFAULT_HEADER = [
    f" PlainOntology file, format {FORMAT_VERSION}",
    " Lines ending in [auto] come from the sources below and are refreshed by `plainonto refresh`.",
    " Every other line is yours and is never overwritten. Format: see SPEC.md in the PlainOntology repo.",
]

# Starts with a letter (any script) or _; then letters, digits, combining marks
# and other non-ASCII printable characters, or _ . : / -.
NAME = r"[^\W\d](?:[\w.:/-]|(?![\s\x80-\x9f])[^\x00-\x7f])*"
_NAME = NAME
_QSTR = r'"((?:[^"\\]|\\.)*)"'
_LANG = r"(?:@([A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*))?"
_TYPE = r'(enum\((?:"(?:[^"\\]|\\.)*"|[^)"])*\)|[^\W\d]\w*)'
_FLAGS = r"((?:\s+(?:" + "|".join(FLAGS) + r"))*)"
_NUMBER = r"(-?\d+(?:\.\d+)?)"

_TAG_RE = re.compile(r"\s+\[(auto|proposed)(?::\s*([^\]]*?))?\s*\]\s*$")
_HAS_RE = re.compile(rf"^has\s+({_NAME})\s*:\s*{_TYPE}{_FLAGS}(?:\s+{_QSTR})?$")
_REL_RE = re.compile(rf"^({_NAME})\s*->\s*({_NAME}){_FLAGS}(?:\s+{_QSTR})?$")
_ISA_RE = re.compile(rf"^is_a\s+({_NAME})$")
_SAMEAS_RE = re.compile(r"^same_as\s+(\S+)$")
_TEXT_RE = re.compile(rf"^(label|definition|synonym|note)\s+{_QSTR}{_LANG}$")
_UNIQUE_RE = re.compile(rf"^unique\s*\(\s*({_NAME}(?:\s*,\s*{_NAME})*)\s*\)$")
_MINMAX_RE = re.compile(rf"^(min|max)\s+{_NUMBER}$")
_LENGTH_RE = re.compile(r"^length\s+(\d*)\s*\.\.\s*(\d*)$")
_PATTERN_RE = re.compile(rf"^pattern\s+{_QSTR}$")
_FORMAT_RE = re.compile(r"^format\s+([A-Za-z][\w-]*)$")
_CONCEPT_RE = re.compile(rf"^concept\s+({_NAME})$")
_PREFIX_RE = re.compile(r"^prefix\s+([A-Za-z_][\w-]*)?:\s*<([^>]*)>$")
_SOURCE_RE = re.compile(r"^source\s+(\S.*?)$")


class ParseError(ValueError):
    def __init__(self, line_no: int, message: str, line: str = ""):
        self.line_no = line_no
        self.line = line
        super().__init__(f"line {line_no}: {message}" + (f"\n  {line.strip()}" if line else ""))


def _split_tag(line: str) -> tuple[str, Tag | None]:
    m = _TAG_RE.search(line)
    if not m:
        return line.rstrip(), None
    return line[: m.start()].rstrip(), Tag(m.group(1), (m.group(2) or "").strip())


def _flags(text: str) -> tuple[str, ...]:
    return tuple(text.split())


def parse_statement(line: str) -> Statement:
    """Parse one statement line (without indentation). Raises ValueError."""
    stripped = line.strip()
    statement = _parse(stripped)
    statement.raw = stripped
    return statement


def _parse(stripped: str) -> Statement:
    if stripped.startswith("#"):
        return Statement("comment", value=stripped[1:])
    body, tag = _split_tag(stripped)
    if m := _HAS_RE.match(body):
        return Statement("has", name=m[1], target=m[2], flags=_flags(m[3]), value=unquote(m[4] or ""), tag=tag)
    if m := _ISA_RE.match(body):
        return Statement("is_a", name=m[1], tag=tag)
    if m := _TEXT_RE.match(body):
        return Statement(m[1], value=unquote(m[2]), lang=m[3] or "", tag=tag)
    if m := _SAMEAS_RE.match(body):
        return Statement("same_as", name=m[1], tag=tag)
    if m := _UNIQUE_RE.match(body):
        cols = [c.strip() for c in m[1].split(",")]
        return Statement("unique", name=", ".join(cols), tag=tag)
    if m := _MINMAX_RE.match(body):
        return Statement(m[1], value=m[2], tag=tag)
    if m := _LENGTH_RE.match(body):
        if not (m[1] or m[2]):
            raise ValueError("length needs at least one bound, for example length 1..200 or length ..13")
        return Statement("length", value=f"{m[1]}..{m[2]}", tag=tag)
    if m := _PATTERN_RE.match(body):
        pattern = unquote(m[1])
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"pattern is not a valid regular expression: {exc}") from None
        return Statement("pattern", value=pattern, tag=tag)
    if m := _FORMAT_RE.match(body):
        return Statement("format", name=m[1], tag=tag)
    if m := _REL_RE.match(body):
        return Statement("rel", name=m[1], target=m[2], flags=_flags(m[3]), value=unquote(m[4] or ""), tag=tag)
    raise ValueError(
        "not a statement; expected has NAME : TYPE, NAME -> CONCEPT, is_a, unique (...), "
        "label, definition, synonym, same_as, note, or under a property: min, max, length, pattern, format"
    )


def _indent(raw: str) -> int:
    expanded = raw.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def loads(text: str) -> Ontology:
    onto = Ontology(from_file=True)
    onto.bom = text.startswith("\ufeff")
    text = text[1:] if onto.bom else text
    first = True
    if "\r\n" in text:
        onto.newline = "\r\n"
    current: Concept | None = None
    last_prop: Statement | None = None
    prop_indent = 0
    pending: list[str] = []  # column-0 comments (and blank lines between them) not yet placed
    pending_gap = 0  # blank lines before the first pending comment
    held: list[Statement] = []  # comments at a property's indentation, before its child lines?
    blanks = 0

    def release_held() -> None:
        """At a concept header or the end of the file: held comments up to the last
        indented one stay in this concept; column-0 comments after it belong to what
        follows (the next concept's leading comments, or the end of the file)."""
        nonlocal held, pending, pending_gap
        if not held or current is None:
            held = []
            return
        last = max((i for i, c in enumerate(held) if not c.col0), default=-1)
        current.statements.extend(held[: last + 1])
        tail, held = held[last + 1 :], []
        if tail:
            pending, pending_gap = [], tail[0].blank_before
            for i, c in enumerate(tail):
                if i:
                    pending.extend([""] * c.blank_before)
                pending.append(c.raw)

    def take_pending() -> list[Statement]:
        """The column-0 comments waiting for a place, as comment lines."""
        nonlocal pending, pending_gap
        out: list[Statement] = []
        waiting = pending_gap
        for entry in pending:
            if not entry:
                waiting += 1
                continue
            comment = parse_statement(entry)
            comment.col0, comment.blank_before, waiting = True, waiting, 0
            out.append(comment)
        pending, pending_gap = [], 0
        return out

    for no, raw in enumerate(text.split("\n"), start=1):
        raw = raw.rstrip("\r")
        if not raw.strip():
            blanks += 1
            continue
        gap, blanks = blanks, 0
        if first:  # blank lines at the very start of the file
            onto.leading_blanks, gap, first = gap, 0, False
        indent = _indent(raw)
        line = raw.strip()
        typed = raw.lstrip()  # as typed, trailing spaces included
        if indent:
            if current is None:
                raise ParseError(no, "indented line outside a concept", raw)
            try:
                statement = parse_statement(line)
            except ValueError as exc:
                raise ParseError(no, str(exc), raw) from None
            statement.raw = typed
            statement.blank_before = gap
            under_prop = last_prop is not None and indent > prop_indent
            if statement.kind == "comment" and last_prop is not None and not under_prop:
                held.extend(take_pending())  # column-0 comments above it keep their place
                held.append(statement)  # placed once the next line shows where it belongs
                continue
            if held:
                if under_prop and last_prop is not None:
                    last_prop.children.extend(held)
                else:
                    current.statements.extend(held)
                held = []
            target = last_prop.children if under_prop and last_prop is not None else current.statements
            if pending:  # column-0 comments inside a concept stay where they are
                target.extend(take_pending())
                statement.blank_before = gap
            if under_prop:
                if statement.kind not in CHILD_KINDS:
                    raise ParseError(
                        no, f"'{statement.kind}' cannot sit under a property; indent it like the property line", raw
                    )
                target.append(statement)
                continue
            if statement.kind in RULE_KINDS:
                raise ParseError(no, f"'{statement.kind}' belongs under a property line; indent it below one", raw)
            current.statements.append(statement)
            if statement.is_property:
                last_prop, prop_indent = statement, indent
            elif statement.kind != "comment":
                last_prop = None
            continue
        if held and current is not None and line.startswith("#"):
            comment = parse_statement(line)
            comment.raw, comment.col0, comment.blank_before = typed, True, gap
            held.append(comment)
            continue
        if held and current is not None:
            release_held()
        if current is None and onto.preamble:
            onto.preamble.extend([("blank", "", "")] * gap)
        if line.startswith("#"):
            if current is None:
                onto.comments.append(line[1:])
                onto.preamble.append(("comment", line[1:], typed))
            else:
                if pending:
                    pending.extend([""] * gap)
                else:
                    pending_gap = gap
                pending.append(typed)
            continue
        last_prop = None
        body, tag = _split_tag(line)
        if m := _CONCEPT_RE.match(body):
            name = m[1]
            if name in onto.concepts:
                raise ParseError(no, f"concept {name} is defined twice", raw)
            blank_before = pending_gap if pending else gap
            if current is None:
                # Comments directly above the first concept belong to it, unless
                # they are the whole header.
                tail = 0
                while tail < len(onto.preamble) and onto.preamble[-1 - tail][0] == "comment":
                    tail += 1
                if not gap and 0 < tail < len(onto.preamble):
                    pending = [raw_line for _, _, raw_line in onto.preamble[-tail:]]
                    del onto.preamble[-tail:]
                    del onto.comments[-tail:]
                    blank_before = 0
                    while onto.preamble and onto.preamble[-1][0] == "blank":
                        onto.preamble.pop()
                        blank_before += 1
                elif gap:
                    while onto.preamble and onto.preamble[-1][0] == "blank":
                        onto.preamble.pop()
            elif pending:
                pending.extend([""] * gap)
            current = Concept(name, tag, leading_comments=pending, raw_header=typed, blank_before=blank_before)
            pending, pending_gap = [], 0
            onto.concepts[name] = current
            continue
        if tag is not None:
            raise ParseError(no, "tags are only allowed on concept and statement lines", raw)
        if current is not None:
            raise ParseError(no, "prefix and source lines must come before the first concept", raw)
        if m := _PREFIX_RE.match(body):
            if (m[1] or "") in onto.prefixes:
                onto.duplicate_prefixes.append(m[1] or "")
            onto.prefixes[m[1] or ""] = m[2]
            onto.preamble.append(("prefix", m[1] or "", typed))
            continue
        if m := _SOURCE_RE.match(body):
            onto.sources.append(m[1])
            onto.preamble.append(("source", m[1], typed))
            continue
        raise ParseError(no, "expected concept, prefix, source or a # comment", raw)
    release_held()
    onto.trailing_comments = pending
    onto.trailing_blank_before = pending_gap
    onto.final_newline = text.endswith("\n") or not text
    onto.trailing_blanks = max(blanks - 1, 0) if onto.final_newline else blanks
    while onto.preamble and onto.preamble[-1][0] == "blank":
        onto.preamble.pop()
    return onto


def _lead(statement: Statement, indent: str, tail: bool = False) -> str:
    """Every line, comments included, is written at the indentation of where it
    belongs: two spaces per level. A comment typed at the start of the line stays
    there, unless it is the last line of its block (then it would read back as
    belonging to what follows, so it is indented)."""
    return "" if statement.col0 and not tail else indent


def _tail_start(lines: list[Statement]) -> int:
    """Index of the first of the comments that end a block (after its last statement)."""
    last = max((i for i, s in enumerate(lines) if s.kind != "comment"), default=-1)
    return last + 1


def dump_statement(statement: Statement, indent: str = "  ", tail: bool = False) -> list[str]:
    lines = [""] * statement.blank_before + [f"{_lead(statement, indent, tail)}{statement.text()}"]
    end = _tail_start(statement.children)
    for i, c in enumerate(statement.children):
        lines += [""] * c.blank_before + [f"{_lead(c, indent + '  ', i >= end)}{c.text()}"]
    return lines


def dump_concept(concept: Concept, first: bool = False) -> list[str]:
    # Comments above the first concept read back as its own only when nothing
    # separates them from it (otherwise they would join the file header).
    lines = [x for x in concept.leading_comments if x] if first else list(concept.leading_comments)
    header = concept.header()
    if concept.raw_header and _split_tag(concept.raw_header)[1] == concept.tag:
        header = concept.raw_header
    lines.append(header)
    end = _tail_start(concept.statements)
    for i, s in enumerate(concept.statements):
        lines.extend(dump_statement(s, tail=i >= end))
    return lines


def _preamble(onto: Ontology) -> list[str]:
    prefixes, sources = dict(onto.prefixes), list(onto.sources)
    if not onto.preamble:
        header = [] if onto.from_file else [f"#{c}" for c in (onto.comments or DEFAULT_HEADER)]
        head = [f"prefix {p}: <{iri}>" for p, iri in prefixes.items()] + [f"source {s}" for s in sources]
        return header + (["", *head] if head and header else head)
    out: list[str] = []
    written: set[str] = set()
    last_head = max((i for i, (kind, _, _) in enumerate(onto.preamble) if kind in ("prefix", "source")), default=-1)
    for i, (kind, value, raw) in enumerate(onto.preamble):
        if kind == "comment":
            out.append(raw)
        elif kind == "blank":
            out.append("")
        elif kind == "prefix" and value in prefixes:
            out.append(raw)  # as typed, duplicates included
            written.add(value)
        elif kind == "source" and value in sources:
            sources.remove(value)
            out.append(raw)
        if i == last_head:  # new prefixes and sources go after the existing ones
            out += [f"prefix {p}: <{iri}>" for p, iri in prefixes.items() if p not in written]
            out += [f"source {s}" for s in sources]
            prefixes, sources = {}, []
    new_lines = [f"prefix {p}: <{iri}>" for p, iri in prefixes.items() if p not in written] + [
        f"source {s}" for s in sources
    ]
    if new_lines:
        out += ["", *new_lines]
    return out


def _ends_in_comments_after_more(out: list[str], onto: Ontology) -> bool:
    """The header written so far ends in comments with something else above them
    (a source line, a blank line): with no gap, a reader would take those comments
    as the first concept's own."""
    header = out[onto.leading_blanks :]
    run = 0
    while run < len(header) and header[-1 - run].lstrip().startswith("#"):
        run += 1
    return 0 < run < len(header)


def dumps(onto: Ontology) -> str:
    out = [""] * onto.leading_blanks + _preamble(onto)
    for i, concept in enumerate(onto.concepts.values()):
        gap = concept.blank_before if out else 0
        if i == 0 and out and (any(concept.leading_comments) or _ends_in_comments_after_more(out, onto)):
            gap = max(gap, 1)  # keep the header's comments and the concept's own apart
        out.extend([""] * gap)
        out.extend(dump_concept(concept, first=i == 0))
    if onto.trailing_comments:
        out.extend([""] * onto.trailing_blank_before)
        out.extend(onto.trailing_comments)
    text = "\n".join(out) + "\n" * ((1 if onto.final_newline else 0) + onto.trailing_blanks)
    return ("\ufeff" if onto.bom else "") + text


def load(path: str | Path) -> Ontology:
    return loads(Path(path).read_bytes().decode("utf-8"))  # bytes: keep CRLF and a BOM visible


def _shape(onto: Ontology) -> list:
    """Which line, comments included, sits under which concept and property: what a
    write must never change."""

    def key(s: Statement) -> tuple:
        return (s.kind, s.value.strip()) if s.kind == "comment" else (s.kind, s.name)

    return [
        (
            c.name,
            [x.strip() for x in c.leading_comments if x.strip()],
            [(key(s), [key(ch) for ch in s.children]) for s in c.statements],
        )
        for c in onto.concepts.values()
    ]


def save(path: str | Path, onto: Ontology) -> None:
    """Write the file, after checking that what is written reads back. A file
    that would not read back is never written."""
    text = dumps(onto)
    try:
        again = loads(text)
    except ParseError as exc:
        raise ValueError(
            f"refusing to write {path}: the result would not read back ({exc}). "
            "This is a bug in PlainOntology; please report it with the source that caused it."
        ) from None
    if _shape(again) != _shape(onto):
        raise ValueError(
            f"refusing to write {path}: a line would read back under a different concept or property. "
            "This is a bug in PlainOntology; please report it with the source that caused it."
        )
    Path(path).write_text(text, encoding="utf-8", newline=onto.newline)

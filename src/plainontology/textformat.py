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

NAME = r"[^\W\d][\w.:/-]*"  # starts with a letter (any script) or _
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
    onto = Ontology()
    current: Concept | None = None
    last_prop: Statement | None = None
    prop_indent = 0
    pending_comments: list[str] = []
    blanks = 0
    for no, raw in enumerate(text.lstrip("﻿").split("\n"), start=1):
        raw = raw.rstrip("\r")
        if not raw.strip():
            blanks += 1
            continue
        gap, blanks = blanks, 0
        indent = _indent(raw)
        line = raw.strip()
        if indent:
            if current is None:
                raise ParseError(no, "indented line outside a concept", raw)
            try:
                statement = parse_statement(line)
            except ValueError as exc:
                raise ParseError(no, str(exc), raw) from None
            statement.blank_before = gap
            if last_prop is not None and indent > prop_indent:
                if statement.kind not in CHILD_KINDS:
                    raise ParseError(
                        no, f"'{statement.kind}' cannot sit under a property; indent it like the property line", raw
                    )
                last_prop.children.append(statement)
                continue
            if statement.kind in RULE_KINDS:
                raise ParseError(no, f"'{statement.kind}' belongs under a property line; indent it below one", raw)
            current.statements.append(statement)
            if statement.is_property:
                last_prop, prop_indent = statement, indent
            elif statement.kind != "comment":
                last_prop = None
            continue
        last_prop = None
        if current is None and gap and onto.preamble:
            onto.preamble.append(("blank", ""))
        if line.startswith("#"):
            if current is None:
                onto.comments.append(line[1:])
                onto.preamble.append(("comment", line[1:]))
            else:
                pending_comments.append(line[1:])
            continue
        body, tag = _split_tag(line)
        if m := _CONCEPT_RE.match(body):
            name = m[1]
            if name in onto.concepts:
                raise ParseError(no, f"concept {name} is defined twice", raw)
            if current is None and not gap:
                # Comments directly above the first concept belong to it, unless
                # they are the whole header.
                tail = 0
                while tail < len(onto.preamble) and onto.preamble[-1 - tail][0] == "comment":
                    tail += 1
                if 0 < tail < len(onto.preamble):
                    pending_comments = [text for _, text in onto.preamble[-tail:]]
                    del onto.preamble[-tail:]
                    del onto.comments[-tail:]
            current = Concept(name, tag, leading_comments=pending_comments, raw_header=line)
            pending_comments = []
            onto.concepts[name] = current
            continue
        if tag is not None:
            raise ParseError(no, "tags are only allowed on concept and statement lines", raw)
        if current is not None:
            raise ParseError(no, "prefix and source lines must come before the first concept", raw)
        if m := _PREFIX_RE.match(body):
            onto.prefixes[m[1] or ""] = m[2]
            onto.preamble.append(("prefix", m[1] or ""))
            continue
        if m := _SOURCE_RE.match(body):
            onto.sources.append(m[1])
            onto.preamble.append(("source", m[1]))
            continue
        raise ParseError(no, "expected concept, prefix, source or a # comment", raw)
    onto.trailing_comments = pending_comments
    while onto.preamble and onto.preamble[-1][0] == "blank":
        onto.preamble.pop()
    return onto


def dump_statement(statement: Statement, indent: str = "  ") -> list[str]:
    lines = [""] * statement.blank_before + [f"{indent}{statement.text()}"]
    for c in statement.children:
        lines += [""] * c.blank_before + [f"{indent}  {c.text()}"]
    return lines


def dump_concept(concept: Concept) -> list[str]:
    lines = [f"#{c}" for c in concept.leading_comments]
    header = concept.header()
    if concept.raw_header and _split_tag(concept.raw_header)[1] == concept.tag:
        header = concept.raw_header
    lines.append(header)
    for s in concept.statements:
        lines.extend(dump_statement(s))
    return lines


def _preamble(onto: Ontology) -> list[str]:
    out: list[str] = []
    prefixes, sources = dict(onto.prefixes), list(onto.sources)
    if not onto.preamble:
        out = [f"#{c}" for c in (onto.comments or DEFAULT_HEADER)]
        head = [f"prefix {p}: <{iri}>" for p, iri in prefixes.items()] + [f"source {s}" for s in sources]
        return out + (["", *head] if head else [])
    last_head = max((i for i, (kind, _) in enumerate(onto.preamble) if kind in ("prefix", "source")), default=-1)
    for i, (kind, value) in enumerate(onto.preamble):
        if kind == "comment":
            out.append(f"#{value}")
        elif kind == "blank":
            out.append("")
        elif kind == "prefix" and value in prefixes:
            out.append(f"prefix {value}: <{prefixes.pop(value)}>")
        elif kind == "source" and value in sources:
            sources.remove(value)
            out.append(f"source {value}")
        if i == last_head:  # new prefixes and sources go after the existing ones
            out += [f"prefix {p}: <{iri}>" for p, iri in prefixes.items()] + [f"source {s}" for s in sources]
            prefixes, sources = {}, []
    if prefixes or sources:
        out += [""] + [f"prefix {p}: <{iri}>" for p, iri in prefixes.items()] + [f"source {s}" for s in sources]
    return out


def dumps(onto: Ontology) -> str:
    out = _preamble(onto)
    for concept in onto.concepts.values():
        out.append("")
        out.extend(dump_concept(concept))
    if onto.trailing_comments:
        out.append("")
        out.extend(f"#{c}" for c in onto.trailing_comments)
    return "\n".join(out) + "\n"


def load(path: str | Path) -> Ontology:
    return loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path: str | Path, onto: Ontology) -> None:
    """Write the file, after checking that what is written reads back. A file
    that would not read back is never written."""
    text = dumps(onto)
    try:
        loads(text)
    except ParseError as exc:
        raise ValueError(
            f"refusing to write {path}: the result would not read back ({exc}). "
            "This is a bug in PlainOntology; please report it with the source that caused it."
        ) from None
    Path(path).write_text(text, encoding="utf-8", newline="\n")

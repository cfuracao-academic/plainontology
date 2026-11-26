"""Read and write the PlainOntology plain text format. See SPEC.md."""

from __future__ import annotations

import re
from pathlib import Path

from .model import FLAGS, Concept, Ontology, Statement, Tag

FORMAT_VERSION = "0.1"

DEFAULT_HEADER = [
    f" PlainOntology file, format {FORMAT_VERSION}",
    " Lines ending in [auto] come from the sources below and are refreshed by `plainonto refresh`.",
    " Every other line is yours and is never overwritten. Format: see SPEC.md in the PlainOntology repo.",
]

_NAME = r"[A-Za-z_][\w.:/-]*"
_QSTR = r'"((?:[^"\\]|\\.)*)"'
_TYPE = r"(enum\([^)]*\)|[A-Za-z_]\w*)"
_FLAGS = r"((?:\s+(?:" + "|".join(FLAGS) + r"))*)"

_TAG_RE = re.compile(r"\s+\[(auto|proposed)(?::\s*([^\]]*?))?\s*\]\s*$")
_HAS_RE = re.compile(rf"^has\s+({_NAME})\s*:\s*{_TYPE}{_FLAGS}(?:\s+{_QSTR})?$")
_REL_RE = re.compile(rf"^({_NAME})\s*->\s*({_NAME}){_FLAGS}(?:\s+{_QSTR})?$")
_ISA_RE = re.compile(rf"^is_a\s+({_NAME})$")
_SAMEAS_RE = re.compile(r"^same_as\s+(\S+)$")
_TEXT_RE = re.compile(rf"^(label|definition|synonym|note)\s+{_QSTR}$")
_CONCEPT_RE = re.compile(rf"^concept\s+({_NAME})$")
_PREFIX_RE = re.compile(r"^prefix\s+([A-Za-z_][\w-]*)?:\s*<([^>]*)>$")
_SOURCE_RE = re.compile(r"^source\s+(\S.*?)$")


class ParseError(ValueError):
    def __init__(self, line_no: int, message: str, line: str = ""):
        self.line_no = line_no
        self.line = line
        super().__init__(f"line {line_no}: {message}" + (f"\n  {line.strip()}" if line else ""))


def _unquote(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append("\n" if nxt == "n" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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
    if stripped.startswith("#"):
        return Statement("comment", value=stripped[1:])
    body, tag = _split_tag(stripped)
    if m := _HAS_RE.match(body):
        return Statement("has", name=m[1], target=m[2], flags=_flags(m[3]), value=_unquote(m[4] or ""), tag=tag)
    if m := _ISA_RE.match(body):
        return Statement("is_a", name=m[1], tag=tag)
    if m := _TEXT_RE.match(body):
        return Statement(m[1], value=_unquote(m[2]), tag=tag)
    if m := _SAMEAS_RE.match(body):
        return Statement("same_as", name=m[1], tag=tag)
    if m := _REL_RE.match(body):
        return Statement("rel", name=m[1], target=m[2], flags=_flags(m[3]), value=_unquote(m[4] or ""), tag=tag)
    raise ValueError(
        "not a statement; expected one of: has NAME : TYPE, NAME -> CONCEPT, is_a, "
        "label, definition, synonym, same_as, note"
    )


def loads(text: str) -> Ontology:
    onto = Ontology()
    current: Concept | None = None
    pending_comments: list[str] = []
    for no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        indented = raw[:1] in (" ", "\t")
        line = raw.strip()
        if indented:
            if current is None:
                raise ParseError(no, "indented line outside a concept", raw)
            try:
                current.statements.append(parse_statement(line))
            except ValueError as exc:
                raise ParseError(no, str(exc), raw) from None
            continue
        if line.startswith("#"):
            (onto.comments if current is None and not onto.concepts else pending_comments).append(line[1:])
            continue
        body, tag = _split_tag(line)
        if m := _CONCEPT_RE.match(body):
            name = m[1]
            if name in onto.concepts:
                raise ParseError(no, f"concept {name} is defined twice", raw)
            current = Concept(name, tag, leading_comments=pending_comments)
            pending_comments = []
            onto.concepts[name] = current
            continue
        if tag is not None:
            raise ParseError(no, "tags are only allowed on concept and statement lines", raw)
        if current is not None:
            raise ParseError(no, "prefix and source lines must come before the first concept", raw)
        if m := _PREFIX_RE.match(body):
            onto.prefixes[m[1] or ""] = m[2]
            continue
        if m := _SOURCE_RE.match(body):
            onto.sources.append(m[1])
            continue
        raise ParseError(no, "expected concept, prefix, source or a # comment", raw)
    onto.trailing_comments = pending_comments
    return onto


def dump_concept(concept: Concept) -> list[str]:
    lines = [f"#{c}" for c in concept.leading_comments]
    lines.append(concept.header())
    lines.extend(f"  {s.render()}" for s in concept.statements)
    return lines


def dumps(onto: Ontology) -> str:
    out: list[str] = [f"#{c}" for c in (onto.comments or DEFAULT_HEADER)]
    head: list[str] = []
    for prefix, iri in onto.prefixes.items():
        head.append(f"prefix {prefix}: <{iri}>")
    for source in onto.sources:
        head.append(f"source {source}")
    if head:
        out += [""] + head
    for concept in onto.concepts.values():
        out.append("")
        out.extend(dump_concept(concept))
    if onto.trailing_comments:
        out.append("")
        out.extend(f"#{c}" for c in onto.trailing_comments)
    return "\n".join(out) + "\n"


def load(path: str | Path) -> Ontology:
    return loads(Path(path).read_text(encoding="utf-8"))


def save(path: str | Path, onto: Ontology) -> None:
    Path(path).write_text(dumps(onto), encoding="utf-8", newline="\n")

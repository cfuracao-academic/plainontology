"""In-memory model of a PlainOntology file.

An ontology is a list of concepts. Each concept holds statements, one per line
in the text file. A statement is either extracted from a source (tagged
``[auto]``), proposed by an agent (tagged ``[proposed]``), or written by a
person (no tag). People's lines are never overwritten by a refresh.

Property lines (``has`` and relations) can carry indented child lines: labels,
synonyms and notes about the property, and validation rules (min, max,
length, pattern, format).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

DATATYPES = ("text", "integer", "decimal", "boolean", "date", "datetime", "binary", "json", "any")
FLAGS = ("key", "required", "unique", "many")
TEXT_KINDS = ("label", "definition", "synonym", "note")
RULE_KINDS = ("min", "max", "length", "pattern", "format")
CHILD_KINDS = TEXT_KINDS + RULE_KINDS + ("comment",)

# Prefixes every file can use without declaring them.
WELL_KNOWN_PREFIXES = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "sh": "http://www.w3.org/ns/shacl#",
    "schema": "https://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "prov": "http://www.w3.org/ns/prov#",
}


_SIMPLE_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def quote(text: str) -> str:
    """Render a double-quoted string.

    Escapes ``\\``, ``"``, new line, carriage return and tab, and writes every
    other control or line-separator character as ``\\uXXXX``, so a string can
    never break a line of the file.
    """
    out = []
    for ch in text:
        if ch in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[ch])
        elif unicodedata.category(ch) in ("Cc", "Zl", "Zp") or ch in "\x85\u2028\u2029":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def unquote(text: str) -> str:
    """Undo ``quote``. Only the escapes ``quote`` writes are interpreted; any other
    backslash is kept as written, so ``pattern "^\\d+$"`` means what it says."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            simple = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", text[i + 2 : i + 6] or ""):
                out.append(chr(int(text[i + 2 : i + 6], 16)))
                i += 6
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def number_text(value: object) -> str:
    """A number as plain decimal text, never in exponent form (``1e-05`` becomes ``0.00001``)."""
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if not d.is_finite():
        return str(value)
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


_PLAIN_ENUM_VALUE = re.compile(r'^[^|()"\s\\][^|()"\\]*(?<!\s)$')


def make_enum(values: list) -> str:
    """``enum(a|b)``; values that contain ``|``, brackets, quotes or spaces at
    either end are written quoted: ``enum(S|"M (medium)"|"L|XL")``."""
    parts = []
    for v in values:
        text = str(v)
        plain = _PLAIN_ENUM_VALUE.match(text) and not any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in text)
        parts.append(text if plain else quote(text))
    return f"enum({'|'.join(parts)})" if parts else "text"


def enum_values(datatype: str) -> list[str] | None:
    """Return the allowed values of an ``enum(a|b)`` datatype, else None."""
    if not (datatype.startswith("enum(") and datatype.endswith(")")):
        return None
    inner, values, i = datatype[5:-1], [], 0
    while i < len(inner):
        if inner[i] == '"':
            j = i + 1
            while j < len(inner) and inner[j] != '"':
                j += 2 if inner[j] == "\\" else 1
            values.append(unquote(inner[i + 1 : j]))
            i = j + 1
            while i < len(inner) and inner[i] != "|":
                i += 1
            i += 1
            continue
        j = inner.find("|", i)
        j = len(inner) if j < 0 else j
        if inner[i:j].strip():
            values.append(inner[i:j].strip())
        i = j + 1
    return values


def unique_columns(name: str) -> list[str]:
    return [c.strip() for c in name.split(",") if c.strip()]


def length_bounds(value: str) -> tuple[int | None, int | None]:
    low, _, high = value.partition("..")
    return (int(low) if low.strip() else None, int(high) if high.strip() else None)


@dataclass(frozen=True)
class Tag:
    kind: str  # "auto" or "proposed"
    detail: str = ""

    def render(self) -> str:
        detail = re.sub(r"\s+", " ", self.detail.replace("]", ")").replace("[", "(")).strip()
        return f"[{self.kind}: {detail}]" if detail else f"[{self.kind}]"


AUTO = Tag("auto")


@dataclass
class Statement:
    kind: str  # is_a, has, rel, unique, label, definition, synonym, same_as, note, comment, or a rule kind
    name: str = ""
    target: str = ""
    flags: tuple[str, ...] = ()
    value: str = ""
    tag: Tag | None = None
    lang: str = ""
    children: list[Statement] = field(default_factory=list)
    # The line as a person typed it (without indentation), written back as is so
    # a refresh never changes the text of a person's line. Cleared on any edit.
    raw: str = field(default="", compare=False)
    blank_before: int = field(default=0, compare=False)  # blank lines above it in the file
    col0: bool = field(default=False, compare=False)  # a comment written at the start of the line
    verbatim: bool = field(default=False, compare=False)  # a comment written back exactly as typed

    @property
    def is_auto(self) -> bool:
        return self.tag is not None and self.tag.kind == "auto"

    @property
    def is_proposed(self) -> bool:
        return self.tag is not None and self.tag.kind == "proposed"

    @property
    def is_property(self) -> bool:
        return self.kind in ("has", "rel")

    def key(self) -> tuple[str, ...]:
        """Identity used by merge: one line per key per concept (or property).

        ``has`` and relation lines share the property namespace, so a person
        can replace an extracted attribute with a relation of the same name.
        """
        if self.is_property:
            return ("prop", self.name)
        if self.kind in ("label", "definition"):
            return (self.kind, self.lang)
        if self.kind in ("synonym", "note"):
            return (self.kind, self.value, self.lang)
        if self.kind in ("is_a", "same_as"):
            return (self.kind, self.name)
        if self.kind == "unique":
            return ("unique", ", ".join(sorted(unique_columns(self.name))))
        if self.kind == "pattern":  # several patterns may apply; all must match
            return (self.kind, self.value)
        if self.kind in RULE_KINDS:
            return (self.kind,)
        return (self.kind, self.value)

    def body(self) -> str:
        """The line without its tag (and without child lines)."""
        flags = "".join(f" {f}" for f in self.flags)
        desc = f" {quote(self.value)}" if self.value else ""
        if self.kind == "is_a":
            return f"is_a {self.name}"
        if self.kind == "has":
            return f"has {self.name} : {self.target}{flags}{desc}"
        if self.kind == "rel":
            return f"{self.name} -> {self.target}{flags}{desc}"
        if self.kind == "same_as":
            return f"same_as {self.name}"
        if self.kind == "unique":
            return f"unique ({self.name})"
        if self.kind == "comment":
            return f"#{self.value}"
        if self.kind in ("min", "max", "length"):
            return f"{self.kind} {self.value}"
        if self.kind == "pattern":
            return f"pattern {quote(self.value)}"
        if self.kind == "format":
            return f"format {self.name}"
        return f"{self.kind} {quote(self.value)}" + (f"@{self.lang}" if self.lang else "")

    def render(self) -> str:
        """The canonical text of the line (used to compare lines)."""
        if self.tag is None or self.kind == "comment":
            return self.body()
        return f"{self.body()} {self.tag.render()}"

    def text(self) -> str:
        """What is written to the file: a person's own text when there is one."""
        if self.raw and not self.is_auto:
            return self.raw
        return self.render()

    def child(self, kind: str) -> Statement | None:
        return next((c for c in self.children if c.kind == kind), None)


@dataclass
class Concept:
    name: str
    tag: Tag | None = None
    statements: list[Statement] = field(default_factory=list)
    leading_comments: list[str] = field(default_factory=list)
    raw_header: str = field(default="", compare=False)
    blank_before: int = field(default=1, compare=False)  # blank lines above the concept

    def header(self) -> str:
        return f"concept {self.name}" + (f" {self.tag.render()}" if self.tag else "")

    def first(self, kind: str, lang: str | None = None) -> Statement | None:
        return next((s for s in self.statements if s.kind == kind and (lang is None or s.lang == lang)), None)

    def all(self, *kinds: str) -> list[Statement]:
        return [s for s in self.statements if s.kind in kinds]

    def properties(self) -> list[Statement]:
        return self.all("has", "rel")

    def prop(self, name: str) -> Statement | None:
        return next((s for s in self.properties() if s.name == name), None)

    def definition(self) -> str:
        s = self.first("definition", "") or self.first("definition", "en") or self.first("definition")
        return s.value if s else ""

    def add(self, statement: Statement) -> bool:
        """Add a statement unless one with the same key exists. Returns True if added."""
        if any(s.key() == statement.key() for s in self.statements):
            return False
        self.statements.append(statement)
        return True

    def add_property(self, statement: Statement, warnings: list[str] | None, what: str, fallback: str = "") -> str:
        """Add a property line; if its name is taken, use ``fallback`` (or the name
        with _2, _3 ...) and say so in ``warnings``, so no extracted line is lost.
        Returns the name used."""
        if self.add(statement):
            return statement.name
        wanted = statement.name
        base = fallback if fallback and fallback != wanted else f"{wanted}_2"
        name, n = base, 2
        while self.prop(name) is not None:
            n += 1
            name = f"{fallback}_{n}" if fallback and fallback != wanted else f"{wanted}_{n}"
        statement.name = name
        self.statements.append(statement)
        if warnings is not None:
            warnings.append(f"{what} would be property {wanted} of {self.name}, which is taken; it is {name}")
        return name


@dataclass
class Ontology:
    comments: list[str] = field(default_factory=list)
    prefixes: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    concepts: dict[str, Concept] = field(default_factory=dict)
    trailing_comments: list[str] = field(default_factory=list)
    # (concept, message) pairs found while extracting; never written to the file
    warnings: list[tuple[str, str]] = field(default_factory=list)
    # The lines before the first concept, in file order, as (kind, value, raw text):
    # ("comment", text, raw), ("prefix", name, raw), ("source", spec, raw) or
    # ("blank", "", ""). Empty for new files.
    preamble: list[tuple[str, str, str]] = field(default_factory=list)
    from_file: bool = False  # read from a file (then no default header is added)
    newline: str = "\n"  # the file's line ending, kept on write
    trailing_blank_before: int = 1
    trailing_blanks: int = 0  # blank lines at the very end of the file
    final_newline: bool = True
    proposals_removed: bool = field(default=False, compare=False)  # set by accepted()
    duplicate_prefixes: list[str] = field(default_factory=list)

    def add_concept(self, concept: Concept) -> Concept:
        """Add a concept, folding its statements into an existing one of the same name."""
        existing = self.concepts.get(concept.name)
        if existing is None:
            self.concepts[concept.name] = concept
            return concept
        for s in concept.statements:
            existing.add(s)
        return existing

    def accepted(self) -> Ontology:
        """A copy without [proposed] concepts and lines, which do not count until
        a person accepts them (used by the exports)."""
        import copy

        if self.proposals_removed:
            return self
        out = copy.deepcopy(self)
        out.proposals_removed = True
        out.concepts = {n: c for n, c in out.concepts.items() if not (c.tag is not None and c.tag.kind == "proposed")}
        for c in out.concepts.values():
            c.statements = [s for s in c.statements if not s.is_proposed]
            for s in c.statements:
                s.children = [ch for ch in s.children if not ch.is_proposed]
        return out

    def find(self, name: str) -> Concept | None:
        """Look a concept up by name, ignoring case."""
        if name in self.concepts:
            return self.concepts[name]
        lowered = name.lower()
        return next((c for n, c in self.concepts.items() if n.lower() == lowered), None)

    def split_target(self, text: str) -> tuple[str, str]:
        """``Concept`` or ``Concept.property`` -> (concept, property). Concept names may
        contain dots themselves (``sales.Order``): an existing concept of the whole name
        wins, then the longest existing concept name before a dot."""
        if self.find(text) is not None or "." not in text:
            return text, ""
        cut = len(text)
        while (cut := text.rfind(".", 0, cut)) > 0:
            if self.find(text[:cut]) is not None:
                return text[:cut], text[cut + 1 :]
        return text, ""

    def prefix_iri(self, prefix: str) -> str | None:
        return self.prefixes.get(prefix) or WELL_KNOWN_PREFIXES.get(prefix)

    def parents(self, concept: Concept) -> list[Concept]:
        """Ancestors along is_a, nearest first, without cycles."""
        out: list[Concept] = []
        seen = {concept.name}
        queue = [s.name for s in concept.all("is_a")]
        while queue:
            name = queue.pop(0)
            if name in seen or name not in self.concepts:
                continue
            seen.add(name)
            parent = self.concepts[name]
            out.append(parent)
            queue.extend(s.name for s in parent.all("is_a"))
        return out

    def all_properties(self, concept: Concept) -> list[Statement]:
        """Own properties first, then inherited ones not overridden."""
        props: dict[str, Statement] = {}
        for c in [concept, *self.parents(concept)]:
            for s in c.properties():
                props.setdefault(s.name, s)
        return list(props.values())

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

from dataclasses import dataclass, field

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


def quote(text: str) -> str:
    """Render a double-quoted string with backslash escapes."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def enum_values(datatype: str) -> list[str] | None:
    """Return the allowed values of an ``enum(a|b)`` datatype, else None."""
    if datatype.startswith("enum(") and datatype.endswith(")"):
        inner = datatype[5:-1]
        return [v.strip() for v in inner.split("|") if v.strip()]
    return None


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
        return f"[{self.kind}: {self.detail}]" if self.detail else f"[{self.kind}]"


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
        if self.tag is None or self.kind == "comment":
            return self.body()
        return f"{self.body()} {self.tag.render()}"

    def child(self, kind: str) -> Statement | None:
        return next((c for c in self.children if c.kind == kind), None)


@dataclass
class Concept:
    name: str
    tag: Tag | None = None
    statements: list[Statement] = field(default_factory=list)
    leading_comments: list[str] = field(default_factory=list)

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


@dataclass
class Ontology:
    comments: list[str] = field(default_factory=list)
    prefixes: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    concepts: dict[str, Concept] = field(default_factory=dict)
    trailing_comments: list[str] = field(default_factory=list)
    # (concept, message) pairs found while extracting; never written to the file
    warnings: list[tuple[str, str]] = field(default_factory=list)

    def add_concept(self, concept: Concept) -> Concept:
        """Add a concept, folding its statements into an existing one of the same name."""
        existing = self.concepts.get(concept.name)
        if existing is None:
            self.concepts[concept.name] = concept
            return concept
        for s in concept.statements:
            existing.add(s)
        return existing

    def find(self, name: str) -> Concept | None:
        """Look a concept up by name, ignoring case."""
        if name in self.concepts:
            return self.concepts[name]
        lowered = name.lower()
        return next((c for n, c in self.concepts.items() if n.lower() == lowered), None)

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

"""Export SHACL shapes, so any SHACL engine can validate RDF data against the file.

One ``sh:NodeShape`` per concept, targeting its class (same IRIs as the Turtle
export). Each property becomes a ``sh:property`` with:

* ``sh:datatype`` (xsd) or ``sh:class`` (relations to concepts in the file)
* ``sh:minCount 1`` for required and key; ``sh:maxCount 1`` unless ``many``
* ``sh:in`` for enums
* ``sh:minInclusive`` / ``sh:maxInclusive`` from min and max
* ``sh:minLength`` / ``sh:maxLength`` from length
* ``sh:pattern`` from pattern, and from the email, uri and uuid formats
* ``sh:name`` / ``sh:description`` from the property's labels and description

Inherited properties are checked too: a concept's shape refers to its parents'
shapes with ``sh:node``, so the shapes file needs no class hierarchy beside it.
MySQL's case-insensitive patterns (``(?i)...``) use ``sh:flags "i"``.
"""

from __future__ import annotations

import re

from ..model import Concept, Ontology, Statement, enum_values, length_bounds
from .rdf import XSD, Namer, base_of, literal, typed

_STANDARD = ("rdf", "rdfs", "sh", "xsd")

FORMAT_PATTERNS = {
    "email": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    "uri": r"^[A-Za-z][A-Za-z0-9+.-]*:\S+$",
    "iri": r"^[A-Za-z][A-Za-z0-9+.-]*:\S+$",
    "uuid": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
}


def _number(value: str, integer: bool) -> str:
    """Exact, from the text (no float rounding of big integers)."""
    if re.fullmatch(r"-?\d+", value):
        return typed(value, "xsd:integer" if integer else "xsd:decimal")
    return typed(value, "xsd:decimal")


def _property(n: Namer, c: Concept, s: Statement) -> str:
    parts = [f"sh:path {n.prop(c.name, s.name)}"]
    label = next((x for x in s.children if x.kind == "label" and x.lang in ("", "en")), None)
    parts.append(f"sh:name {literal(label.value if label else s.name)}")
    if s.value:
        parts.append(f"sh:description {literal(s.value)}")
    if "required" in s.flags or "key" in s.flags:
        parts.append("sh:minCount 1")
    if "many" not in s.flags:
        parts.append("sh:maxCount 1")
    if s.kind == "rel":
        if s.target in n.onto.concepts:
            parts.append(f"sh:class {n.iri(s.target)}")
        else:
            parts.append("sh:nodeKind sh:IRI")
    else:
        values = enum_values(s.target)
        if values is not None:
            parts.append("sh:in ( " + " ".join(literal(v) for v in values) + " )")
        elif s.target in XSD:
            parts.append(f"sh:datatype {XSD[s.target]}")
        integer = s.target == "integer"
        patterns = []
        for rule in s.children:
            if rule.kind == "min":
                parts.append(f"sh:minInclusive {_number(rule.value, integer)}")
            elif rule.kind == "max":
                parts.append(f"sh:maxInclusive {_number(rule.value, integer)}")
            elif rule.kind == "length":
                low, high = length_bounds(rule.value)
                if low is not None:
                    parts.append(f"sh:minLength {low}")
                if high is not None:
                    parts.append(f"sh:maxLength {high}")
            elif rule.kind == "pattern":
                patterns.append(rule.value)
            elif rule.kind == "format" and rule.name in FORMAT_PATTERNS:
                patterns.append(FORMAT_PATTERNS[rule.name])
        for p in patterns:
            if p.startswith("(?i)"):  # XPath regex has no inline flags: use sh:flags
                parts.append(f'sh:node [ sh:pattern {literal(p[4:])} ; sh:flags "i" ]')
            else:
                parts.append(f"sh:pattern {literal(p)}")
    return "[\n        " + " ;\n        ".join(parts) + "\n    ]"


def to_shacl(onto: Ontology, base: str | None = None) -> str:
    onto = onto.accepted()
    base = base_of(onto, base)
    n = Namer(onto, base, _STANDARD)
    blocks = []
    for c in onto.concepts.values():
        pairs = [("a", "sh:NodeShape"), ("sh:targetClass", n.iri(c.name))]
        if c.definition():
            pairs.append(("rdfs:comment", literal(c.definition())))
        # A subclass's instances must also satisfy the parent's shape (sh:node), so
        # inherited properties are checked with this shapes file alone.
        pairs += [("sh:node", n.iri(s.name + "Shape")) for s in c.all("is_a") if s.name in onto.concepts]
        pairs += [("sh:property", _property(n, c, s)) for s in c.properties()]
        body = " ;\n    ".join(f"{p} {o}" for p, o in pairs)
        blocks.append(f"{n.iri(c.name + 'Shape')}\n    {body} .\n")
    return "\n".join(n.head()) + "\n\n" + "\n".join(blocks)

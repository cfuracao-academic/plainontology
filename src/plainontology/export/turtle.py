"""Export to RDF Turtle using OWL for structure and SKOS for meaning.

* concept            -> owl:Class (rdfs:label = concept name)
* label / synonym    -> skos:prefLabel / skos:altLabel, with language tags
* definition / note  -> skos:definition / skos:note
* is_a / same_as     -> rdfs:subClassOf / owl:equivalentClass
* has                -> owl:DatatypeProperty with an xsd range
* relation           -> owl:ObjectProperty with the target class as range
* single-valued      -> also owl:FunctionalProperty
* required / key     -> owl:minCardinality 1 restriction on the class
* key, unique (a, b) -> owl:hasKey
* lines under a property (label, synonym, note) describe the property itself

Properties are scoped to their concept (``Book/title``), so two concepts can
each have a ``name`` without clashing. Validation rules (min, max, length,
pattern, format) are not OWL; export them with ``--to shacl``.
"""

from __future__ import annotations

from ..model import Concept, Ontology, Statement, enum_values, unique_columns
from .rdf import TEXT_PREDICATES, XSD, Namer, base_of, block, literal

_STANDARD = ("owl", "rdf", "rdfs", "xsd", "skos")


def _text_pairs(statements: list[Statement]) -> list[tuple[str, str]]:
    return [(TEXT_PREDICATES[s.kind], literal(s.value, s.lang)) for s in statements if s.kind in TEXT_PREDICATES]


def _concept(n: Namer, c: Concept) -> list[str]:
    pairs = [("a", "owl:Class"), ("rdfs:label", literal(c.name)), *_text_pairs(c.statements)]
    for s in c.statements:
        if s.kind == "is_a" and (r := n.ref(s.name)):
            pairs.append(("rdfs:subClassOf", r))
        elif s.kind == "same_as" and (r := n.ref(s.name)):
            pairs.append(("owl:equivalentClass", r))
    own = {s.name for s in c.properties()}
    for s in c.properties():
        if "required" in s.flags or "key" in s.flags:
            pairs.append(
                (
                    "rdfs:subClassOf",
                    f"[ a owl:Restriction ; owl:onProperty {n.prop(c.name, s.name)} ; "
                    f'owl:minCardinality "1"^^xsd:nonNegativeInteger ]',
                )
            )
    keys = [[s.name] for s in c.properties() if "key" in s.flags]
    keys += [unique_columns(s.name) for s in c.all("unique")]
    for cols in keys:
        if all(col in own for col in cols):
            pairs.append(("owl:hasKey", "( " + " ".join(n.prop(c.name, col) for col in cols) + " )"))
    return [block(n.iri(c.name), pairs)] + [_prop(n, c, s) for s in c.properties()]


def _prop(n: Namer, c: Concept, s: Statement) -> str:
    types = ["owl:DatatypeProperty" if s.kind == "has" else "owl:ObjectProperty"]
    if "many" not in s.flags:
        types.append("owl:FunctionalProperty")
    pairs = [("a", ", ".join(types)), ("rdfs:label", literal(s.name)), ("rdfs:domain", n.iri(c.name))]
    if s.kind == "has":
        values = enum_values(s.target)
        if values is not None:
            pairs.append(("rdfs:range", "xsd:string"))
            pairs.append(("skos:note", literal("one of: " + ", ".join(values))))
        else:
            pairs.append(("rdfs:range", XSD.get(s.target, "rdfs:Literal")))
    elif r := n.ref(s.target):
        pairs.append(("rdfs:range", r))
    if s.value:
        pairs.append(("skos:definition", literal(s.value)))
    pairs += _text_pairs(s.children)
    if "unique" in s.flags:
        pairs.append(("skos:note", literal("unique")))
    return block(n.prop(c.name, s.name), pairs)


def to_turtle(onto: Ontology, base: str | None = None) -> str:
    onto = onto.accepted()
    base = base_of(onto, base)
    n = Namer(onto, base, _STANDARD)
    blocks: list[str] = []
    for c in onto.concepts.values():
        blocks += _concept(n, c)
    ontology_iri = base.rstrip("#/") or base
    comment = literal("Exported by PlainOntology.")
    head = [*n.head(), "", f"<{ontology_iri}> a owl:Ontology ;", f"    rdfs:comment {comment} .", ""]
    return "\n".join(head) + "\n" + "\n".join(blocks)


# Kept for callers that imported it from here in 0.1.
from .rdf import DEFAULT_BASE  # noqa: E402,F401

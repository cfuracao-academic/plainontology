"""Export to RDF Turtle using OWL for structure and SKOS for meaning.

* concept            -> owl:Class (rdfs:label = concept name)
* label / synonym    -> skos:prefLabel / skos:altLabel
* definition / note  -> skos:definition / skos:note
* is_a / same_as     -> rdfs:subClassOf / owl:equivalentClass
* has                -> owl:DatatypeProperty with an xsd range
* relation           -> owl:ObjectProperty with the target class as range
* single-valued      -> also owl:FunctionalProperty
* required           -> owl:minCardinality 1 restriction on the class

Properties are scoped to their concept (``Book/title``), so two concepts can
each have a ``name`` without clashing.
"""

from __future__ import annotations

from urllib.parse import quote as urlquote

from ..model import Concept, Ontology, Statement, enum_values

DEFAULT_BASE = "https://example.org/plainontology#"

XSD = {
    "text": "xsd:string",
    "integer": "xsd:integer",
    "decimal": "xsd:decimal",
    "boolean": "xsd:boolean",
    "date": "xsd:date",
    "datetime": "xsd:dateTime",
    "binary": "xsd:base64Binary",
    "json": "rdfs:Literal",
    "any": "rdfs:Literal",
}

_STANDARD = ("owl", "rdf", "rdfs", "xsd", "skos")


def _literal(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )
    return f'"{escaped}"'


class _Writer:
    def __init__(self, onto: Ontology, base: str):
        self.onto = onto
        self.base = base
        self.used: set[str] = set(_STANDARD)

    def iri(self, local: str) -> str:
        return f"<{self.base}{urlquote(local, safe='/-._~')}>"

    def ref(self, name: str) -> str | None:
        if name in self.onto.concepts:
            return self.iri(name)
        if name.startswith(("http://", "https://")):
            return f"<{name}>"
        prefix, sep, local = name.partition(":")
        if sep:
            ns = self.onto.prefix_iri(prefix)
            if ns is None:
                return None
            self.used.add(prefix)
            return f"<{ns}{urlquote(local, safe='/-._~#')}>"
        return self.iri(name)

    def block(self, subject: str, pairs: list[tuple[str, str]]) -> str:
        body = " ;\n    ".join(f"{p} {o}" for p, o in pairs)
        return f"{subject}\n    {body} .\n"

    def concept(self, c: Concept) -> list[str]:
        pairs = [("a", "owl:Class"), ("rdfs:label", _literal(c.name))]
        mapping = {"label": "skos:prefLabel", "definition": "skos:definition", "synonym": "skos:altLabel", "note": "skos:note"}
        for s in c.statements:
            if s.kind in mapping:
                pairs.append((mapping[s.kind], _literal(s.value)))
            elif s.kind == "is_a" and (r := self.ref(s.name)):
                pairs.append(("rdfs:subClassOf", r))
            elif s.kind == "same_as" and (r := self.ref(s.name)):
                pairs.append(("owl:equivalentClass", r))
        for s in c.properties():
            if "required" in s.flags or "key" in s.flags:
                prop = self.iri(f"{c.name}/{s.name}")
                pairs.append((
                    "rdfs:subClassOf",
                    f"[ a owl:Restriction ; owl:onProperty {prop} ; owl:minCardinality \"1\"^^xsd:nonNegativeInteger ]",
                ))
        blocks = [self.block(self.iri(c.name), pairs)]
        blocks += [self.prop(c, s) for s in c.properties()]
        return blocks

    def prop(self, c: Concept, s: Statement) -> str:
        types = ["owl:DatatypeProperty" if s.kind == "has" else "owl:ObjectProperty"]
        if "many" not in s.flags:
            types.append("owl:FunctionalProperty")
        pairs = [("a", ", ".join(types)), ("rdfs:label", _literal(s.name)), ("rdfs:domain", self.iri(c.name))]
        if s.kind == "has":
            values = enum_values(s.target)
            pairs.append(("rdfs:range", "xsd:string" if values is not None else XSD.get(s.target, "rdfs:Literal")))
            if values is not None:
                pairs.append(("skos:note", _literal("one of: " + ", ".join(values))))
        elif r := self.ref(s.target):
            pairs.append(("rdfs:range", r))
        if s.value:
            pairs.append(("skos:definition", _literal(s.value)))
        if "unique" in s.flags or "key" in s.flags:
            pairs.append(("skos:note", _literal("unique identifier" if "key" in s.flags else "unique")))
        return self.block(self.iri(f"{c.name}/{s.name}"), pairs)


def to_turtle(onto: Ontology, base: str | None = None) -> str:
    base = base or onto.prefixes.get("") or DEFAULT_BASE
    w = _Writer(onto, base)
    blocks: list[str] = []
    for c in onto.concepts.values():
        blocks += w.concept(c)
    ontology_iri = base.rstrip("#/") or base
    head = [f"@prefix {p}: <{onto.prefix_iri(p)}> ." for p in sorted(w.used)]
    head += ["", f"<{ontology_iri}> a owl:Ontology ;", f"    rdfs:comment {_literal('Exported by PlainOntology.')} .", ""]
    return "\n".join(head) + "\n" + "\n".join(blocks)

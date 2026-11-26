"""Shared helpers for the RDF exports (Turtle and SHACL)."""

from __future__ import annotations

from urllib.parse import quote as urlquote

from ..model import Ontology

DEFAULT_BASE = "https://example.org/plainontology#"

XSD = {
    "text": "xsd:string",
    "integer": "xsd:integer",
    "decimal": "xsd:decimal",
    "boolean": "xsd:boolean",
    "date": "xsd:date",
    "datetime": "xsd:dateTime",
    "binary": "xsd:base64Binary",
}

TEXT_PREDICATES = {"label": "skos:prefLabel", "definition": "skos:definition", "synonym": "skos:altLabel",
                   "note": "skos:note"}


def literal(text: str, lang: str = "") -> str:
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )
    return f'"{escaped}"' + (f"@{lang}" if lang else "")


def typed(value: str, datatype: str) -> str:
    return f'"{value}"^^{datatype}'


class Namer:
    """Turns concept and property names into IRIs, tracking prefixes used."""

    def __init__(self, onto: Ontology, base: str, standard: tuple[str, ...]):
        self.onto = onto
        self.base = base
        self.used: set[str] = set(standard)

    def iri(self, local: str) -> str:
        return f"<{self.base}{urlquote(local, safe='/-._~')}>"

    def prop(self, concept: str, prop: str) -> str:
        return self.iri(f"{concept}/{prop}")

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
            return f"<{ns}{urlquote(local, safe='/-._~#')}>"
        return self.iri(name)

    def head(self) -> list[str]:
        return [f"@prefix {p}: <{self.onto.prefix_iri(p)}> ." for p in sorted(self.used)]


def block(subject: str, pairs: list[tuple[str, str]]) -> str:
    body = " ;\n    ".join(f"{p} {o}" for p, o in pairs)
    return f"{subject}\n    {body} .\n"


def base_of(onto: Ontology, base: str | None) -> str:
    return base or onto.prefixes.get("") or DEFAULT_BASE

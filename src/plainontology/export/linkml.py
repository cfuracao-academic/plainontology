"""Export to a LinkML schema (YAML), so the file plugs into the LinkML toolchain
(generators for JSON Schema, Pydantic, SQL DDL, SHACL, docs)."""

from __future__ import annotations

import json
import re

from ..model import Ontology, enum_values
from ..naming import pascal, safe_name
from .turtle import DEFAULT_BASE

RANGES = {
    "text": "string",
    "integer": "integer",
    "decimal": "decimal",
    "boolean": "boolean",
    "date": "date",
    "datetime": "datetime",
    "binary": "string",
    "json": "string",
    "any": "string",
}

_PLAIN_KEY = re.compile(r"^[A-Za-z_][\w.-]*$")


def _scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _key(k: str) -> str:
    return k if _PLAIN_KEY.match(k) and k not in ("true", "false", "null", "yes", "no", "on", "off") else json.dumps(k, ensure_ascii=False)


def _yaml(obj: dict, indent: int = 0) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for k, v in obj.items():
        key = _key(str(k))
        if isinstance(v, dict):
            if v:
                lines.append(f"{pad}{key}:")
                lines += _yaml(v, indent + 2)
            else:
                lines.append(f"{pad}{key}: {{}}")
        elif isinstance(v, list):
            if v:
                lines.append(f"{pad}{key}:")
                lines += [f"{pad}  - {_scalar(item)}" for item in v]
            else:
                lines.append(f"{pad}{key}: []")
        else:
            lines.append(f"{pad}{key}: {_scalar(v)}")
    return lines


def to_linkml(onto: Ontology, name: str = "ontology", base: str | None = None) -> str:
    base = base or onto.prefixes.get("") or DEFAULT_BASE
    schema_name = safe_name(name).replace(".", "_")
    prefixes = {"linkml": "https://w3id.org/linkml/", schema_name: base}
    for p, iri in onto.prefixes.items():
        if p:
            prefixes[p] = iri
    classes: dict = {}
    enums: dict = {}
    for c in onto.concepts.values():
        cls: dict = {}
        if label := c.first("label"):
            cls["title"] = label.value
        if d := c.definition():
            cls["description"] = d
        parents = [s.name for s in c.all("is_a") if s.name in onto.concepts]
        if parents:
            cls["is_a"] = parents[0]
        if aliases := [s.value for s in c.all("synonym")]:
            cls["aliases"] = aliases
        mappings = [s.name for s in c.all("same_as")]
        for m in mappings:
            prefix = m.partition(":")[0]
            if prefix not in prefixes and (iri := onto.prefix_iri(prefix)):
                prefixes[prefix] = iri
        if mappings:
            cls["exact_mappings"] = mappings
        if notes := [s.value for s in c.all("note")]:
            cls["comments"] = notes
        attrs: dict = {}
        for s in c.properties():
            a: dict = {}
            if s.kind == "has":
                values = enum_values(s.target)
                if values is not None:
                    enum_name = pascal(f"{c.name} {s.name} enum")
                    enums[enum_name] = {"permissible_values": {v: {} for v in values}}
                    a["range"] = enum_name
                else:
                    a["range"] = RANGES.get(s.target, "string")
            else:
                a["range"] = s.target if s.target in onto.concepts else "uriorcurie"
            if "key" in s.flags:
                a["identifier"] = True
            if "required" in s.flags:
                a["required"] = True
            if "many" in s.flags:
                a["multivalued"] = True
            if s.value:
                a["description"] = s.value
            attrs[s.name] = a
        if attrs:
            cls["attributes"] = attrs
        classes[c.name] = cls

    doc: dict = {
        "id": base.rstrip("#/") or base,
        "name": schema_name,
        "description": "Exported by PlainOntology.",
        "prefixes": prefixes,
        "default_prefix": schema_name,
        "default_range": "string",
        "imports": ["linkml:types"],
        "classes": classes,
    }
    if enums:
        doc["enums"] = enums
    return "\n".join(_yaml(doc)) + "\n"

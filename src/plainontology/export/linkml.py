"""Export to a LinkML schema (YAML), so the file plugs into the LinkML toolchain
(generators for JSON Schema, Pydantic, SQL DDL, SHACL, docs).

* label / definition (untagged or English) -> title / description; other
  languages go to ``annotations`` (``label_fr``, ``definition_fr``)
* synonyms -> ``aliases``; language-tagged ones -> ``structured_aliases``
* is_a: the first parent -> ``is_a``; further parents -> ``mixins`` (marked
  ``mixin: true`` when they are never used as a first parent)
* key, unique flag, unique (a, b) -> ``identifier`` and ``unique_keys``
* min, max, pattern -> ``minimum_value``, ``maximum_value``, ``pattern``;
  length and the email/uri/uuid formats become a pattern when there is none
"""

from __future__ import annotations

import json
import re

from ..model import Ontology, Statement, enum_values, length_bounds, unique_columns
from ..naming import pascal, safe_name
from .rdf import DEFAULT_BASE
from .shacl import FORMAT_PATTERNS

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
    plain = _PLAIN_KEY.match(k) and k not in ("true", "false", "null", "yes", "no", "on", "off")
    return k if plain else json.dumps(k, ensure_ascii=False)


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
            if not v:
                lines.append(f"{pad}{key}: []")
                continue
            lines.append(f"{pad}{key}:")
            for item in v:
                if isinstance(item, dict):
                    sub = _yaml(item, indent + 4)
                    lines.append(f"{pad}  - {sub[0].strip()}")
                    lines += sub[1:]
                else:
                    lines.append(f"{pad}  - {_scalar(item)}")
        else:
            lines.append(f"{pad}{key}: {_scalar(v)}")
    return lines


def _number(value: str):
    f = float(value)
    return int(f) if f.is_integer() else f


def _texts(element: dict, statements: list[Statement]) -> None:
    """Fill title, description, aliases, annotations and comments from text lines."""
    annotations: dict[str, str] = {}
    for s in statements:
        if s.kind in ("label", "definition"):
            field = "title" if s.kind == "label" else "description"
            if s.lang in ("", "en") and field not in element:
                element[field] = s.value
            else:
                annotations[f"{s.kind}_{s.lang or 'und'}"] = s.value
        elif s.kind == "synonym":
            if s.lang in ("", "en"):
                element.setdefault("aliases", []).append(s.value)
            else:
                element.setdefault("structured_aliases", []).append({"literal_form": s.value, "in_language": s.lang})
        elif s.kind == "note":
            element.setdefault("comments", []).append(s.value)
    if annotations:
        element["annotations"] = annotations


def _rules(attr: dict, prop: Statement) -> None:
    patterns, notes = [], []
    for rule in prop.children:
        if rule.kind == "min":
            attr["minimum_value"] = _number(rule.value)
        elif rule.kind == "max":
            attr["maximum_value"] = _number(rule.value)
        elif rule.kind == "pattern":
            patterns.append(rule.value)
    lengths = [r for r in prop.children if r.kind == "length"]
    formats = [r for r in prop.children if r.kind == "format"]
    if patterns:
        attr["pattern"] = patterns[0]
        notes += [f"length {r.value}" for r in lengths] + [f"format {r.name}" for r in formats]
    else:
        for r in formats:
            if r.name in FORMAT_PATTERNS and "pattern" not in attr:
                attr["pattern"] = FORMAT_PATTERNS[r.name]
            else:
                notes.append(f"format {r.name}")
        for r in lengths:
            low, high = length_bounds(r.value)
            if "pattern" not in attr:
                attr["pattern"] = "^.{" + f"{low or 0},{high if high is not None else ''}" + "}$"
            else:
                notes.append(f"length {r.value}")
    if notes:
        attr.setdefault("comments", []).extend(notes)


def to_linkml(onto: Ontology, name: str = "ontology", base: str | None = None) -> str:
    base = base or onto.prefixes.get("") or DEFAULT_BASE
    schema_name = safe_name(name).replace(".", "_")
    prefixes = {"linkml": "https://w3id.org/linkml/", schema_name: base}
    for p, iri in onto.prefixes.items():
        if p:
            prefixes[p] = iri

    first_parents = set()
    for c in onto.concepts.values():
        parents = [s.name for s in c.all("is_a") if s.name in onto.concepts]
        if parents:
            first_parents.add(parents[0])

    classes: dict = {}
    enums: dict = {}
    mixin_classes: set[str] = set()
    for c in onto.concepts.values():
        cls: dict = {}
        _texts(cls, c.statements)
        parents = [s.name for s in c.all("is_a") if s.name in onto.concepts]
        if parents:
            cls["is_a"] = parents[0]
        mixins = [p for p in parents[1:] if p not in first_parents]
        if mixins:
            cls["mixins"] = mixins
            mixin_classes.update(mixins)
        for p in parents[1:]:
            if p not in mixins:
                cls.setdefault("comments", []).append(f"also a kind of {p}")
        mappings = [s.name for s in c.all("same_as")]
        for m in mappings:
            prefix = m.partition(":")[0]
            if prefix not in prefixes and (iri := onto.prefix_iri(prefix)):
                prefixes[prefix] = iri
        if mappings:
            cls["exact_mappings"] = mappings

        attrs: dict = {}
        unique_keys: dict = {}
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
            _texts(a, [x for x in s.children if x.kind in ("label", "definition", "synonym", "note")])
            _rules(a, s)
            if "unique" in s.flags:
                unique_keys[f"{s.name}_key"] = {"unique_key_name": f"{s.name}_key", "unique_key_slots": [s.name]}
            attrs[s.name] = a
        for u in c.all("unique"):
            cols = unique_columns(u.name)
            key_name = "_".join(cols) + "_key"
            unique_keys[key_name] = {"unique_key_name": key_name, "unique_key_slots": cols}
        if attrs:
            cls["attributes"] = attrs
        if unique_keys:
            cls["unique_keys"] = unique_keys
        classes[c.name] = cls

    for name_ in mixin_classes:
        classes[name_]["mixin"] = True

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

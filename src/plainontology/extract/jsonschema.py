"""Extract concepts from JSON Schema (draft 7 and 2020-12, local $refs).

The root object and every object in ``$defs``/``definitions`` become concepts.
Nested objects become concepts of their own, linked by a relation. Arrays set
the ``many`` flag. ``description`` becomes a definition (on concepts) or a
property description (on properties). Pydantic models can be extracted by
dumping ``Model.model_json_schema()`` to a file first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..model import AUTO, Concept, Statement, Tag
from ..naming import pascal, safe_name, singular


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _enum_token(value: Any) -> str:
    return re.sub(r"[|()]", "_", str(value)).strip() or "_"


class SchemaWalker:
    def __init__(self, root: dict, label: str):
        self.root = root
        self.label = label
        self.concepts: dict[str, Concept] = {}

    # references -------------------------------------------------------
    def ref_name(self, ref: str) -> str:
        return pascal(ref.rstrip("/").rsplit("/", 1)[-1])

    def resolve(self, ref: str) -> dict | None:
        if not ref.startswith("#"):
            return None
        node: Any = self.root
        for part in [p for p in ref[1:].split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, dict) else None

    def ensure_ref(self, ref: str) -> None:
        name = self.ref_name(ref)
        resolved = self.resolve(ref)
        if name not in self.concepts and resolved is not None and self.is_object(resolved):
            self.object_concept(name, resolved, f"jsonschema {self.label} {ref}")

    # shapes -----------------------------------------------------------
    @staticmethod
    def is_object(ps: dict) -> bool:
        return ps.get("type") == "object" or (
            ps.get("type") is None and ("properties" in ps or "allOf" in ps)
        )

    def unwrap(self, ps: dict) -> dict:
        for key in ("anyOf", "oneOf"):
            if key in ps:
                rest = {k: v for k, v in ps.items() if k != key}
                branches = [b for b in ps[key] if isinstance(b, dict) and b.get("type") != "null"]
                if len(branches) == 1:
                    return self.unwrap({**rest, **branches[0]})
                return {k: v for k, v in rest.items() if k in ("description", "title")}
        t = ps.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            ps = {**ps, "type": non_null[0] if len(non_null) == 1 else None}
        return ps

    # building -----------------------------------------------------------
    def object_concept(self, name: str, schema: dict, detail: str) -> Concept:
        if name in self.concepts:
            return self.concepts[name]
        concept = Concept(name, Tag("auto", detail))
        self.concepts[name] = concept
        if desc := _clean(schema.get("description")):
            concept.add(Statement("definition", value=desc, tag=AUTO))
        title = _clean(schema.get("title"))
        if title and pascal(title) != name:
            concept.add(Statement("label", value=title, tag=AUTO))
        for part in schema.get("allOf", []) or []:
            if not isinstance(part, dict):
                continue
            if "$ref" in part:
                self.ensure_ref(part["$ref"])
                concept.add(Statement("is_a", name=self.ref_name(part["$ref"]), tag=AUTO))
            else:
                self._properties(concept, part)
        self._properties(concept, schema)
        return concept

    def _properties(self, concept: Concept, schema: dict) -> None:
        required = set(schema.get("required", []) or [])
        for pname, ps in (schema.get("properties") or {}).items():
            ps = ps if isinstance(ps, dict) else {}
            concept.add(self._property(concept.name, pname, ps, pname in required))

    def _property(self, owner: str, pname: str, ps: dict, required: bool) -> Statement:
        desc = _clean(ps.get("description"))
        ps = self.unwrap(ps)
        flags = ["required"] if required else []
        many = ps.get("type") == "array"
        if many:
            items = ps.get("items")
            ps = self.unwrap(items if isinstance(items, dict) else {})
            desc = desc or _clean(ps.get("description"))
            flags.append("many")
        kind, target = self._target(owner, pname, ps, many)
        return Statement(kind, name=safe_name(pname), target=target, flags=tuple(flags), value=desc, tag=AUTO)

    def _target(self, owner: str, pname: str, ps: dict, many: bool) -> tuple[str, str]:
        if "$ref" in ps:
            ref = ps["$ref"]
            resolved = self.resolve(ref)
            if resolved is not None and not self.is_object(resolved):
                return self._target(owner, pname, self.unwrap(resolved), many)
            self.ensure_ref(ref)
            return "rel", self.ref_name(ref)
        if self.is_object(ps):
            if ps.get("properties") or ps.get("allOf"):
                base = ps.get("title") or f"{owner} {singular(pname) if many else pname}"
                name = pascal(base)
                self.object_concept(name, ps, f"jsonschema {self.label} {owner}.{pname}")
                return "rel", name
            return "has", "json"
        if "enum" in ps:
            values = [_enum_token(v) for v in ps["enum"] if v is not None]
            return "has", f"enum({'|'.join(values)})" if values else "any"
        if "const" in ps:
            return "has", f"enum({_enum_token(ps['const'])})"
        t, fmt = ps.get("type"), ps.get("format")
        if t == "string":
            if fmt == "date":
                return "has", "date"
            if fmt == "date-time":
                return "has", "datetime"
            if fmt in ("byte", "binary") or ps.get("contentEncoding") == "base64":
                return "has", "binary"
            return "has", "text"
        return "has", {"integer": "integer", "number": "decimal", "boolean": "boolean"}.get(t, "any")


def root_name(schema: dict, path: Path) -> str:
    stem = path.name
    for suffix in (".json", ".schema"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
    return pascal(schema.get("title") or stem)


def extract_jsonschema(path: Path) -> list[Concept]:
    schema = json.loads(path.read_text(encoding="utf-8"))
    walker = SchemaWalker(schema, path.name)
    walker.object_concept(root_name(schema, path), schema, f"jsonschema {path.name}")
    for key in ("$defs", "definitions"):
        for name in schema.get(key, {}) or {}:
            walker.ensure_ref(f"#/{key}/{name}")
    return list(walker.concepts.values())

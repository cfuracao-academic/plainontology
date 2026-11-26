"""Extract concepts from JSON Schema (draft 7 and 2020-12).

* The root object and every object in ``$defs``/``definitions`` become concepts;
  nested objects become concepts of their own, linked by a relation.
* ``$ref`` works within the file and across files (``person.json#/$defs/Person``,
  relative to the referring file). Web URLs are not fetched: the relation keeps
  the referenced name and ``plainonto check`` flags it.
* ``oneOf``/``anyOf`` with several object options becomes a parent concept that
  each option ``is_a``; several enum options merge into one enum; mixed simple
  types become ``any`` with the options noted.
* Arrays set the ``many`` flag. ``description`` becomes a definition (concepts)
  or a property description (properties). ``minimum``, ``maximum``,
  ``minLength``, ``maxLength``, ``pattern`` and ``format`` become rules under
  the property.

Pydantic models can be extracted by dumping ``Model.model_json_schema()`` first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..model import AUTO, Concept, Statement, Tag
from ..naming import pascal, safe_name, singular

_FORMATS = {
    "email",
    "idn-email",
    "uri",
    "uri-reference",
    "iri",
    "iri-reference",
    "uuid",
    "hostname",
    "idn-hostname",
    "ipv4",
    "ipv6",
    "time",
    "duration",
    "uri-template",
    "json-pointer",
    "regex",
}


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _enum_token(value: Any) -> str:
    return re.sub(r"[|()]", "_", str(value)).strip() or "_"


def _num(value) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


class _Shared:
    """State shared by the walkers of every file a schema refers to."""

    def __init__(self):
        self.concepts: dict[str, Concept] = {}
        self.walkers: dict[Path, SchemaWalker] = {}


class SchemaWalker:
    def __init__(self, root: dict, label: str, path: Path | None = None, shared: _Shared | None = None):
        self.root = root
        self.label = label
        self.path = path
        self.shared = shared or _Shared()
        if path is not None:
            self.shared.walkers.setdefault(path, self)

    @property
    def concepts(self) -> dict[str, Concept]:
        return self.shared.concepts

    # references -------------------------------------------------------
    def _walker_for(self, file_part: str) -> SchemaWalker | None:
        if not file_part:
            return self
        if re.match(r"^[a-z][a-z0-9+.-]*://", file_part, re.I) or self.path is None:
            return None
        target = (self.path.parent / file_part).resolve()
        if target not in self.shared.walkers:
            if not target.exists():
                return None
            doc = json.loads(target.read_text(encoding="utf-8"))
            SchemaWalker(doc, target.name, target, self.shared)
        return self.shared.walkers[target]

    def locate(self, ref: str) -> tuple[SchemaWalker | None, str]:
        file_part, _, pointer = ref.partition("#")
        return self._walker_for(file_part), pointer

    def ref_name(self, ref: str) -> str:
        walker, pointer = self.locate(ref)
        if pointer.strip("/"):
            return pascal(pointer.rstrip("/").rsplit("/", 1)[-1])
        if walker is not None and walker is not self:
            return root_name(walker.root, walker.path)
        file_part = ref.partition("#")[0]
        stem = re.sub(r"(\.schema)?\.json$", "", file_part.rstrip("/").rsplit("/", 1)[-1], flags=re.I)
        return pascal(stem or "Thing")

    def resolve_pointer(self, pointer: str) -> dict | None:
        node: Any = self.root
        for part in [p for p in pointer.split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, dict) else None

    def resolve(self, ref: str) -> tuple[SchemaWalker | None, dict | None]:
        walker, pointer = self.locate(ref)
        return walker, (walker.resolve_pointer(pointer) if walker is not None else None)

    def ensure_ref(self, ref: str) -> None:
        name = self.ref_name(ref)
        walker, resolved = self.resolve(ref)
        if name not in self.concepts and walker is not None and resolved is not None and walker.is_object(resolved):
            walker.object_concept(name, resolved, f"jsonschema {walker.label} #{ref.partition('#')[2]}")

    # shapes -----------------------------------------------------------
    @staticmethod
    def is_object(ps: dict) -> bool:
        return ps.get("type") == "object" or (ps.get("type") is None and ("properties" in ps or "allOf" in ps))

    @staticmethod
    def branches(ps: dict) -> list[dict] | None:
        for key in ("anyOf", "oneOf"):
            if key in ps:
                return [b for b in ps[key] if isinstance(b, dict) and b.get("type") != "null"]
        return None

    def unwrap(self, ps: dict) -> dict:
        options = self.branches(ps)
        if options is not None and len(options) == 1:
            rest = {k: v for k, v in ps.items() if k not in ("anyOf", "oneOf")}
            return self.unwrap({**rest, **options[0]})
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
        outer = ps
        ps = self.unwrap(ps)
        flags = ["required"] if required else []
        many = ps.get("type") == "array"
        if many:
            items = ps.get("items")
            ps = self.unwrap(items if isinstance(items, dict) else {})
            desc = desc or _clean(ps.get("description"))
            flags.append("many")
        walker, ps = self._follow(ps)
        kind, target, extra = walker._target(owner, pname, ps, many)
        if extra:
            desc = f"{desc} ({extra})" if desc else extra
        line = Statement(kind, name=safe_name(pname), target=target, flags=tuple(flags), value=desc, tag=AUTO)
        if kind == "has":
            line.children = self._rules(ps, target)
        if many:
            low, high = outer.get("minItems"), outer.get("maxItems")
            if low is not None or high is not None:
                bounds = f"{low or 0} to {high} items" if high is not None else f"at least {low} items"
                line.children.append(Statement("note", value=bounds, tag=AUTO))
        return line

    def _follow(self, ps: dict) -> tuple[SchemaWalker, dict]:
        """Follow a $ref to a non-object schema (an enum or string definition)."""
        walker = self
        for _ in range(10):
            if "$ref" not in ps:
                break
            target_walker, resolved = walker.resolve(ps["$ref"])
            if (
                target_walker is None
                or resolved is None
                or target_walker.is_object(resolved)
                or target_walker.branches(resolved)
            ):
                break
            extra = {k: v for k, v in ps.items() if k != "$ref"}
            walker, ps = target_walker, target_walker.unwrap({**resolved, **extra})
        return walker, ps

    def _rules(self, ps: dict, target: str) -> list[Statement]:
        out: list[Statement] = []
        integer = target == "integer"
        low, high = ps.get("minimum"), ps.get("maximum")
        x_low, x_high = ps.get("exclusiveMinimum"), ps.get("exclusiveMaximum")
        notes = []
        if isinstance(x_low, bool):  # draft 4
            x_low, low = (low if x_low else None), (None if x_low else low)
        if isinstance(x_high, bool):
            x_high, high = (high if x_high else None), (None if x_high else high)
        if x_low is not None:
            if integer:
                low = x_low + 1 if low is None else max(low, x_low + 1)
            else:
                notes.append(f"greater than {_num(x_low)}")
        if x_high is not None:
            if integer:
                high = x_high - 1 if high is None else min(high, x_high - 1)
            else:
                notes.append(f"less than {_num(x_high)}")
        if low is not None:
            out.append(Statement("min", value=_num(low), tag=AUTO))
        if high is not None:
            out.append(Statement("max", value=_num(high), tag=AUTO))
        lo_len, hi_len = ps.get("minLength"), ps.get("maxLength")
        if lo_len is not None or hi_len is not None:
            out.append(
                Statement(
                    "length",
                    value=f"{lo_len if lo_len is not None else ''}..{hi_len if hi_len is not None else ''}",
                    tag=AUTO,
                )
            )
        if isinstance(ps.get("pattern"), str):
            try:
                re.compile(ps["pattern"])
                out.append(Statement("pattern", value=ps["pattern"], tag=AUTO))
            except re.error:
                notes.append(f"pattern {ps['pattern']}")
        if ps.get("format") in _FORMATS:
            out.append(Statement("format", name=ps["format"], tag=AUTO))
        out += [Statement("note", value=n, tag=AUTO) for n in notes]
        return out

    def _union(self, owner: str, pname: str, options: list[dict], many: bool) -> tuple[str, str, str]:
        enums = [o.get("enum", [o["const"]] if "const" in o else None) for o in options]
        if all(e is not None for e in enums):
            values = list(dict.fromkeys(_enum_token(v) for e in enums for v in (e or []) if v is not None))
            return "has", f"enum({'|'.join(values)})", ""
        resolved = []
        for o in options:
            walker, node = self.resolve(o["$ref"]) if "$ref" in o else (self, o)
            resolved.append((o, walker, node))
        if all(node is not None and walker is not None and walker.is_object(node) for _, walker, node in resolved):
            name = pascal(f"{owner} {singular(pname) if many else pname}")
            union = Concept(name, Tag("auto", f"jsonschema {self.label} {owner}.{pname} (one of)"))
            self.concepts.setdefault(name, union)
            members = []
            for i, (o, walker, node) in enumerate(resolved):
                if "$ref" in o:
                    self.ensure_ref(o["$ref"])
                    member = self.ref_name(o["$ref"])
                else:
                    assert walker is not None and node is not None  # checked above
                    member = pascal(node.get("title") or f"{name} option {i + 1}")
                    walker.object_concept(member, node, f"jsonschema {self.label} {owner}.{pname} option {i + 1}")
                members.append(member)
                if member in self.concepts:
                    self.concepts[member].add(Statement("is_a", name=name, tag=AUTO))
            self.concepts[name].add(Statement("definition", value="One of: " + ", ".join(members) + ".", tag=AUTO))
            return "rel", name, ""
        kinds = []
        for _option, option_walker, node in resolved:
            if option_walker is None:
                kinds.append("any")
                continue
            _k, t, _ = option_walker._target(owner, pname, option_walker.unwrap(node or {}), many)
            kinds.append(t)
        return "has", "any", "one of: " + ", ".join(dict.fromkeys(kinds))

    def _target(self, owner: str, pname: str, ps: dict, many: bool) -> tuple[str, str, str]:
        options = self.branches(ps)
        if options is not None and len(options) > 1:
            return self._union(owner, pname, options, many)
        if "$ref" in ps:
            ref = ps["$ref"]
            self.ensure_ref(ref)
            return "rel", self.ref_name(ref), ""
        if self.is_object(ps):
            if ps.get("properties") or ps.get("allOf"):
                base = ps.get("title") or f"{owner} {singular(pname) if many else pname}"
                name = pascal(base)
                self.object_concept(name, ps, f"jsonschema {self.label} {owner}.{pname}")
                return "rel", name, ""
            return "has", "json", ""
        if "enum" in ps:
            values = [_enum_token(v) for v in ps["enum"] if v is not None]
            return ("has", f"enum({'|'.join(values)})", "") if values else ("has", "any", "")
        if "const" in ps:
            return "has", f"enum({_enum_token(ps['const'])})", ""
        t, fmt = ps.get("type"), ps.get("format")
        if t == "string":
            if fmt == "date":
                return "has", "date", ""
            if fmt == "date-time":
                return "has", "datetime", ""
            if fmt in ("byte", "binary") or ps.get("contentEncoding") == "base64":
                return "has", "binary", ""
            return "has", "text", ""
        return "has", {"integer": "integer", "number": "decimal", "boolean": "boolean"}.get(str(t), "any"), ""


def root_name(schema: dict, path: Path | None) -> str:
    stem = path.name if path is not None else "Thing"
    for suffix in (".json", ".schema"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
    return pascal(schema.get("title") or stem)


def extract_jsonschema(path: Path) -> list[Concept]:
    path = path.resolve()
    schema = json.loads(path.read_text(encoding="utf-8"))
    walker = SchemaWalker(schema, path.name, path)
    walker.object_concept(root_name(schema, path), schema, f"jsonschema {path.name}")
    for key in ("$defs", "definitions"):
        for name in schema.get(key, {}) or {}:
            walker.ensure_ref(f"#/{key}/{name}")
    return list(walker.concepts.values())

"""Extract concepts from a dbt project's manifest (``target/manifest.json``).

Run ``dbt parse`` (or any dbt command) first; it writes the manifest.

* Models, seeds and snapshots become concepts; sources become concepts named
  after the source and table (``jaffle_shop.orders`` becomes ``JaffleShopOrder``).
* Descriptions become definitions and property descriptions; documented
  ``data_type`` gives the type.
* Tests and constraints carry over: ``not_null`` gives ``required``,
  ``unique`` gives ``unique``, ``accepted_values`` an enum, ``relationships``
  a relation to the referenced model, ``dbt_utils.accepted_range`` min and
  max, and a ``primary_key`` constraint gives ``key``.
* What a model is built from becomes a note; semantic models add notes for
  their measures and dimensions.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from ..model import AUTO, Concept, Statement, Tag, number_text
from ..naming import concept_name, relation_name, safe_name
from .sqlmodel import enum_type, map_type

MODEL_TYPES = ("model", "seed", "snapshot")
_REF = re.compile(r"""ref\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]+)['"]\s*)?\)""")
_SOURCE = re.compile(r"""source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""")


class _Column:
    def __init__(self, name: str):
        self.name = name
        self.description = ""
        self.datatype = "any"
        self.required = False
        self.unique = False
        self.key = False
        self.enum: list[str] | None = None
        self.target: str | None = None  # concept name of a relationships test
        self.low: Any = None
        self.high: Any = None
        self.inclusive = True
        self.notes: list[str] = []


_PLAIN_COLUMN = re.compile(r'[A-Za-z_][\w$]*|"[^"]+"|`[^`]+`')


def _num(value: Any) -> str | None:
    """A numeric bound as text, or None for dates and other non-numbers."""
    try:
        number = float(str(value).strip("'\""))
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number_text(str(value).strip("'\""))


def _ref_target(text: str, names: dict[str, str]) -> str | None:
    if m := _REF.search(text or ""):
        model = m.group(2) or m.group(1)
        return names.get(f"model:{model}") or concept_name(model)
    if m := _SOURCE.search(text or ""):
        return names.get(f"source:{m.group(1)}.{m.group(2)}")
    return None


def extract_dbt(path: Path, warnings: list[str] | None = None) -> list[Concept]:
    if path.is_dir():
        from . import within_root

        path = path / "target" / "manifest.json" if (path / "target").is_dir() else path / "manifest.json"
        within_root(path)  # target/ could be a link to somewhere else
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    nodes: dict[str, dict] = manifest.get("nodes") or {}
    sources: dict[str, dict] = manifest.get("sources") or {}

    names: dict[str, str] = {}  # "model:orders" / "source:shop.orders" -> concept name
    by_id: dict[str, str] = {}  # unique_id -> concept name
    entries: list[tuple[str, dict, str]] = []
    for uid, node in nodes.items():
        if node.get("resource_type") in MODEL_TYPES:
            cname = concept_name(node["name"])
            names[f"model:{node['name']}"] = cname
            by_id[uid] = cname
            entries.append((uid, node, cname))
    for uid, src in sources.items():
        cname = concept_name(f"{src.get('source_name', '')} {src['name']}")
        names[f"source:{src.get('source_name')}.{src['name']}"] = cname
        by_id[uid] = cname
        entries.append((uid, src, cname))

    columns: dict[str, dict[str, _Column]] = {}
    for uid, node, _cname in entries:
        cols: dict[str, _Column] = {}
        for cname_, col in (node.get("columns") or {}).items():
            c = cols.setdefault(cname_, _Column(col.get("name") or cname_))
            c.description = (col.get("description") or "").strip()
            if col.get("data_type"):
                c.datatype = map_type(str(col["data_type"]))
            for constraint in col.get("constraints") or []:
                kind = constraint.get("type")
                if kind == "not_null":
                    c.required = True
                elif kind == "unique":
                    c.unique = True
                elif kind == "primary_key":
                    c.key = True
                elif kind == "foreign_key":
                    c.target = _ref_target(constraint.get("to") or constraint.get("expression") or "", names)
        columns[uid] = cols

    expression_notes: dict[str, list[str]] = {}
    for node in nodes.values():
        if node.get("resource_type") != "test":
            continue
        meta = node.get("test_metadata") or {}
        attached = node.get("attached_node") or next(
            (d for d in (node.get("depends_on") or {}).get("nodes", []) if d in columns), None
        )
        column = node.get("column_name") or (meta.get("kwargs") or {}).get("column_name")
        if attached not in columns or not column:
            continue
        if column not in columns[attached] and not _PLAIN_COLUMN.fullmatch(column.strip()):
            # A test on an expression, such as (country_code || '-' || order_id): a note, not a property.
            test_name = str(meta.get("name", "test")).replace("_", " ")
            text = column.strip() if column.strip().startswith("(") else f"({column.strip()})"
            expression_notes.setdefault(attached, []).append(f"{test_name} {text}")
            continue
        column = column.strip().strip('"`')
        c = columns[attached].setdefault(column, _Column(column))
        kwargs = meta.get("kwargs") or {}
        test = meta.get("name", "")
        namespace = meta.get("namespace") or ""
        condition = (node.get("config") or {}).get("where")
        if condition and test in ("not_null", "unique"):  # a conditional test is not a rule for every row
            c.notes.append(f"{test.replace('_', ' ')} where {condition}")
        elif test == "not_null":
            c.required = True
        elif test == "unique":
            c.unique = True
        elif test == "accepted_values":
            c.enum = [str(v) for v in kwargs.get("values") or []]
        elif test == "relationships":
            c.target = _ref_target(str(kwargs.get("to", "")), names)
        elif test == "accepted_range" and namespace in ("dbt_utils", ""):
            c.low, c.high = kwargs.get("min_value"), kwargs.get("max_value")
            c.inclusive = kwargs.get("inclusive", True) is not False

    measures: dict[str, list[str]] = {}
    for sm in (manifest.get("semantic_models") or {}).values():
        target = _ref_target(str(sm.get("model", "")), names) or by_id.get(
            next(iter((sm.get("depends_on") or {}).get("nodes", [])), "")
        )
        if not target:
            continue
        for m in sm.get("measures") or []:
            text = f"measure {m.get('name')}: {m.get('agg', '')} of {m.get('expr') or m.get('name')}"
            if m.get("description"):
                text += f" ({m['description'].strip()})"
            measures.setdefault(target, []).append(text)
        for d in sm.get("dimensions") or []:
            measures.setdefault(target, []).append(f"dimension {d.get('name')} ({d.get('type', 'categorical')})")

    concepts: list[Concept] = []
    for uid, node, cname in entries:
        kind = node.get("resource_type", "model")
        where = f"{node.get('source_name')}.{node['name']}" if kind == "source" else node["name"]
        concept = Concept(cname, Tag("auto", f"dbt {kind} {where}"))
        if desc := (node.get("description") or "").strip():
            concept.add(Statement("definition", value=re_space(desc), tag=AUTO))
        for c in columns[uid].values():
            flags = ["key"] if c.key else (["required"] if c.required else [])
            if c.unique and not c.key:
                flags.append("unique")
            if c.target:
                line = Statement(
                    "rel",
                    name=relation_name(c.name),
                    target=c.target,
                    flags=tuple(f for f in flags if f != "unique"),
                    value=re_space(c.description),
                    tag=AUTO,
                )
            else:
                datatype = enum_type(c.enum) if c.enum else c.datatype
                line = Statement(
                    "has",
                    name=safe_name(c.name),
                    target=datatype,
                    flags=tuple(flags),
                    value=re_space(c.description),
                    tag=AUTO,
                )
                for kind, bound in (("min", c.low), ("max", c.high)):
                    if bound is None:
                        continue
                    number = _num(bound)
                    if number is not None and c.inclusive:
                        line.children.append(Statement(kind, value=number, tag=AUTO))
                    else:
                        word = "at least" if kind == "min" else "at most"
                        if not c.inclusive:
                            word = "more than" if kind == "min" else "less than"
                        line.children.append(Statement("note", value=f"{word} {bound}", tag=AUTO))
            line.children += [Statement("note", value=text, tag=AUTO) for text in c.notes]
            concept.add_property(line, warnings, f"dbt {where}: column {c.name}", safe_name(c.name))
        parents = [by_id[d] for d in (node.get("depends_on") or {}).get("nodes", []) if d in by_id]
        if parents:
            concept.add(Statement("note", value="built from: " + ", ".join(dict.fromkeys(parents)), tag=AUTO))
        for text in measures.get(cname, []) + expression_notes.get(uid, []):
            concept.add(Statement("note", value=text, tag=AUTO))
        concepts.append(concept)
    return concepts


def re_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

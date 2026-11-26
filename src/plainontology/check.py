"""Consistency checks on a parsed file, and validation of records against it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .model import DATATYPES, Concept, Ontology, Statement, enum_values


@dataclass
class Issue:
    level: str  # error, warning, info
    concept: str
    message: str

    def render(self) -> str:
        return f"{self.level}: {self.concept}: {self.message}"


def _resolvable(name: str, onto: Ontology) -> bool:
    if name in onto.concepts or name.startswith(("http://", "https://")):
        return True
    prefix, sep, _ = name.partition(":")
    return bool(sep) and onto.prefix_iri(prefix) is not None


def check(onto: Ontology) -> list[Issue]:
    issues: list[Issue] = []
    for c in onto.concepts.values():
        seen: dict[tuple, Statement] = {}
        for s in c.statements:
            if s.kind == "comment":
                continue
            key = s.key()
            if key in seen:
                issues.append(Issue("error", c.name, f"two lines for the same thing: '{seen[key].body()}' and '{s.body()}'"))
            seen[key] = s
            if s.kind == "has" and s.target not in DATATYPES and enum_values(s.target) is None:
                issues.append(Issue("warning", c.name, f"unknown datatype '{s.target}' (use one of {', '.join(DATATYPES)} or enum(a|b))"))
            if s.kind in ("rel", "is_a"):
                target = s.target if s.kind == "rel" else s.name
                if not _resolvable(target, onto):
                    issues.append(Issue("warning", c.name, f"'{target}' is not a concept in this file and not a known prefix"))
            if s.kind == "same_as" and not _resolvable(s.name, onto):
                prefix = s.name.partition(":")[0]
                issues.append(Issue("warning", c.name, f"prefix '{prefix}' is not declared; add a line: prefix {prefix}: <IRI>"))
            if s.is_proposed:
                issues.append(Issue("info", c.name, f"proposed line waiting for review: {s.render()}"))
        if c.tag is not None and c.tag.kind == "proposed":
            issues.append(Issue("info", c.name, "proposed concept waiting for review"))
    return issues


# --- record validation ------------------------------------------------------

_MAX_DEPTH = 6


def _check_value(onto: Ontology, where: str, s: Statement, value: Any, depth: int) -> list[str]:
    if s.kind == "rel":
        if isinstance(value, dict):
            if s.target not in onto.concepts or depth >= _MAX_DEPTH:
                return []
            return validate_record(onto, s.target, value, depth + 1, prefix=f"{where}.")
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return []  # a reference by identifier
        return [f"{where}: expected a {s.target} object or its identifier"]
    t = s.target
    allowed = enum_values(t)
    if allowed is not None:
        return [] if str(value) in allowed else [f"{where}: '{value}' is not one of {', '.join(allowed)}"]
    ok = True
    if t == "text" or t == "binary":
        ok = isinstance(value, str)
    elif t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif t == "decimal":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif t == "boolean":
        ok = isinstance(value, bool)
    elif t in ("date", "datetime"):
        ok = isinstance(value, str)
        if ok:
            try:
                if t == "date":
                    date.fromisoformat(value)
                else:
                    datetime.fromisoformat(re.sub(r"Z$", "+00:00", value))
            except ValueError:
                ok = False
    return [] if ok else [f"{where}: expected {t}, got {type(value).__name__} {value!r}"]


def validate_record(onto: Ontology, concept_name: str, record: Any, depth: int = 0, prefix: str = "") -> list[str]:
    """Check a JSON object against a concept: unknown fields, missing required
    fields, wrong types, enum values, and nested objects. Returns problems found."""
    concept: Concept | None = onto.find(concept_name)
    if concept is None:
        return [f"unknown concept '{concept_name}'"]
    if not isinstance(record, dict):
        return [f"{prefix}{concept.name}: a record must be a JSON object"]
    props = {s.name: s for s in onto.all_properties(concept)}
    issues = [f"{prefix}{k}: not a property of {concept.name}" for k in record if k not in props]
    for name, s in props.items():
        where = f"{prefix}{name}"
        if record.get(name) is None:
            if "required" in s.flags:
                issues.append(f"{where}: required but missing")
            continue
        value = record[name]
        if "many" in s.flags:
            if not isinstance(value, list):
                issues.append(f"{where}: expected a list")
                continue
            for i, item in enumerate(value):
                issues += _check_value(onto, f"{where}[{i}]", s, item, depth)
        elif isinstance(value, list):
            issues.append(f"{where}: expected one value, got a list")
        else:
            issues += _check_value(onto, where, s, value, depth)
    return issues

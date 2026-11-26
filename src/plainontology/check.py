"""Consistency checks on a parsed file, and validation of records against it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from .model import DATATYPES, RULE_KINDS, Concept, Ontology, Statement, enum_values, length_bounds, unique_columns


@dataclass
class Issue:
    level: str  # error, warning, info
    concept: str
    message: str

    def render(self) -> str:
        return f"{self.level}: {self.concept}: {self.message}"


KNOWN_FORMATS = {
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
_NUMERIC = ("integer", "decimal")


def _resolvable(name: str, onto: Ontology) -> bool:
    if name in onto.concepts or name.startswith(("http://", "https://")):
        return True
    prefix, sep, _ = name.partition(":")
    return bool(sep) and onto.prefix_iri(prefix) is not None


def _check_children(c: Concept, prop: Statement, issues: list[Issue]) -> None:
    where = f"{c.name}.{prop.name}"
    seen: dict[tuple, Statement] = {}
    for child in prop.children:
        if child.kind == "comment":
            continue
        key = child.key()
        if key in seen:
            issues.append(
                Issue("error", where, f"two lines for the same thing: '{seen[key].body()}' and '{child.body()}'")
            )
        seen[key] = child
        if child.kind in RULE_KINDS and prop.kind == "rel":
            issues.append(Issue("warning", where, f"'{child.kind}' rules apply to values, not to relations"))
        elif child.kind in ("min", "max") and prop.target not in _NUMERIC:
            issues.append(Issue("warning", where, f"'{child.kind}' needs a numeric property, not {prop.target}"))
        elif child.kind in ("length", "pattern", "format") and prop.target not in ("text", "any"):
            issues.append(Issue("warning", where, f"'{child.kind}' needs a text property, not {prop.target}"))
        if child.kind == "format" and child.name not in KNOWN_FORMATS:
            issues.append(Issue("info", where, f"format '{child.name}' is not checked by validate"))
        if child.is_proposed:
            issues.append(Issue("info", where, f"proposed line waiting for review: {child.render()}"))
    low, high = prop.child("min"), prop.child("max")
    if low and high and float(low.value) > float(high.value):
        issues.append(Issue("error", where, f"min {low.value} is greater than max {high.value}"))


def check(onto: Ontology) -> list[Issue]:
    issues: list[Issue] = []
    for c in onto.concepts.values():
        seen: dict[tuple, Statement] = {}
        props = {s.name for s in onto.all_properties(c)}
        for s in c.statements:
            if s.kind == "comment":
                continue
            key = s.key()
            if key in seen:
                issues.append(
                    Issue("error", c.name, f"two lines for the same thing: '{seen[key].body()}' and '{s.body()}'")
                )
            seen[key] = s
            if s.kind == "has" and s.target not in DATATYPES and enum_values(s.target) is None:
                issues.append(
                    Issue(
                        "warning",
                        c.name,
                        f"unknown datatype '{s.target}' (use one of {', '.join(DATATYPES)} or enum(a|b))",
                    )
                )
            if s.kind in ("rel", "is_a"):
                target = s.target if s.kind == "rel" else s.name
                if not _resolvable(target, onto):
                    issues.append(
                        Issue("warning", c.name, f"'{target}' is not a concept in this file and not a known prefix")
                    )
            if s.kind == "same_as" and not _resolvable(s.name, onto):
                prefix = s.name.partition(":")[0]
                issues.append(
                    Issue("warning", c.name, f"prefix '{prefix}' is not declared; add a line: prefix {prefix}: <IRI>")
                )
            if s.kind == "unique":
                missing = [col for col in unique_columns(s.name) if col not in props]
                if missing:
                    issues.append(
                        Issue("warning", c.name, f"unique ({s.name}) names unknown properties: {', '.join(missing)}")
                    )
            if s.is_property:
                _check_children(c, s, issues)
            if s.is_proposed:
                issues.append(Issue("info", c.name, f"proposed line waiting for review: {s.render()}"))
        if c.tag is not None and c.tag.kind == "proposed":
            issues.append(Issue("info", c.name, "proposed concept waiting for review"))
    return issues


# --- record validation ------------------------------------------------------

_MAX_DEPTH = 6
# The same strict ISO 8601 shapes on every Python version.
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:\S+$")
_HOST = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def _format_ok(fmt: str, value: str) -> bool:
    if fmt in ("email", "idn-email"):
        return bool(_EMAIL.match(value))
    if fmt in ("uri", "iri"):
        return bool(_URI.match(value))
    if fmt == "uuid":
        try:
            UUID(value)
            return True
        except ValueError:
            return False
    if fmt in ("hostname", "idn-hostname"):
        return bool(_HOST.match(value))
    if fmt in ("ipv4", "ipv6"):
        import ipaddress

        try:
            return ipaddress.ip_address(value).version == (4 if fmt == "ipv4" else 6)
        except ValueError:
            return False
    return True


def _rules(where: str, s: Statement, value: Any) -> list[str]:
    out = []
    for rule in s.children:
        if rule.kind in ("min", "max") and isinstance(value, (int, float)) and not isinstance(value, bool):
            bound = float(rule.value)
            if (rule.kind == "min" and value < bound) or (rule.kind == "max" and value > bound):
                out.append(
                    f"{where}: {value} is {'below the minimum' if rule.kind == 'min' else 'above the maximum'} "
                    f"{rule.value}"
                )
        elif rule.kind == "length" and isinstance(value, str):
            low, high = length_bounds(rule.value)
            if (low is not None and len(value) < low) or (high is not None and len(value) > high):
                out.append(f"{where}: length {len(value)} is outside {rule.value}")
        elif rule.kind == "pattern" and isinstance(value, str) and not re.search(rule.value, value):
            out.append(f"{where}: '{value}' does not match the pattern {rule.value}")
        elif rule.kind == "format" and isinstance(value, str) and not _format_ok(rule.name, value):
            out.append(f"{where}: '{value}' is not a valid {rule.name}")
    return out


def _subtypes(onto: Ontology, name: str) -> list[str]:
    """``name`` and every concept that is_a it, directly or indirectly."""
    out = [name]
    for n in out:
        out.extend(
            c.name for c in onto.concepts.values() if c.name not in out and any(s.name == n for s in c.all("is_a"))
        )
    return out


def _check_value(onto: Ontology, where: str, s: Statement, value: Any, depth: int) -> list[str]:
    if s.kind == "rel":
        if isinstance(value, dict):
            if s.target not in onto.concepts or depth >= _MAX_DEPTH:
                return []
            best: list[str] | None = None
            for candidate in _subtypes(onto, s.target):
                found = validate_record(onto, candidate, value, depth + 1, prefix=f"{where}.")
                if best is None or len(found) < len(best):
                    best = found
                if not found:
                    break
            return best or []
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
        shape = _DATE if t == "date" else _DATETIME
        ok = isinstance(value, str) and bool(shape.match(value))
        if ok:
            try:
                if t == "date":
                    date.fromisoformat(value)
                else:
                    datetime.fromisoformat(re.sub(r"Z$", "+00:00", value))
            except ValueError:
                ok = False
    if not ok:
        return [f"{where}: expected {t}, got {type(value).__name__} {value!r}"]
    return _rules(where, s, value)


def validate_record(onto: Ontology, concept_name: str, record: Any, depth: int = 0, prefix: str = "") -> list[str]:
    """Check a JSON object against a concept: unknown fields, missing required
    fields, wrong types, enum values, rules (min, max, length, pattern, format)
    and nested objects. Returns problems found."""
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
            if "required" in s.flags or "key" in s.flags:
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

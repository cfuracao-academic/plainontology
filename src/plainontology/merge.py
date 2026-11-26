"""Merge a fresh extraction into an existing file and report drift.

Rules (see SPEC.md, "Refresh rules"):

1. Lines without a tag, and ``[proposed]`` lines, belong to people. They are
   always kept, in place.
2. ``[auto]`` lines are replaced by what the sources say now. Auto lines the
   sources no longer produce are removed.
3. If a person's line has the same key as an extracted line (for example both
   describe the property ``title``), the person's line wins and the extracted
   one is not written.
4. The same rules apply to the child lines under a property.
5. A concept or property that disappears from the sources is removed only if it
   was extracted and holds no lines of yours. Otherwise it is kept and reported.
6. Renames: when an extracted concept disappears and one new concept appears
   with mostly the same properties, it is treated as a rename and your lines
   move to the new name. The same goes for a property with lines of yours
   under it, when exactly one new property of the same type appears.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .model import Concept, Ontology, Statement

_SYMBOLS = {
    "added": "+",
    "removed": "-",
    "changed": "~",
    "kept_yours": "=",
    "concept_added": "+",
    "concept_removed": "-",
    "concept_kept": "!",
    "property_kept": "!",
    "concept_renamed": ">",
    "property_renamed": ">",
    "warning": "?",
}

# Report lines that are not drift: nothing about the sources changed.
_NOT_DRIFT = ("kept_yours", "warning")

RENAME_SIMILARITY = 0.6


@dataclass
class Change:
    kind: str
    concept: str
    line: str
    previous: str = ""

    def render(self) -> str:
        sym = _SYMBOLS[self.kind]
        if self.kind == "concept_added":
            return f"{sym} concept {self.concept}"
        if self.kind == "concept_removed":
            return f"{sym} concept {self.concept} (gone from the sources)"
        if self.kind == "concept_kept":
            return f"{sym} concept {self.concept} is gone from the sources; kept because it holds your lines"
        if self.kind == "property_kept":
            return f"{sym} {self.concept}: {self.line} is gone from the sources; kept because it holds your lines"
        if self.kind == "concept_renamed":
            return f"{sym} concept {self.previous} renamed to {self.concept}; your lines moved with it"
        if self.kind == "property_renamed":
            return f"{sym} {self.concept}: {self.previous} renamed to {self.line}; your lines moved with it"
        if self.kind == "changed":
            return f"{sym} {self.concept}: {self.line}  (was: {self.previous})"
        if self.kind == "kept_yours":
            return f"{sym} {self.concept}: kept your line {self.line}  (sources say: {self.previous})"
        if self.kind == "warning":
            return f"{sym} warning: concept {self.concept} is {self.line}"
        return f"{sym} {self.concept}: {self.line}"


@dataclass
class DriftReport:
    changes: list[Change] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return any(c.kind not in _NOT_DRIFT for c in self.changes)

    @property
    def warnings(self) -> list[Change]:
        return [c for c in self.changes if c.kind == "warning"]

    def add(self, kind: str, concept: str, line: str, previous: str = "") -> None:
        self.changes.append(Change(kind, concept, line, previous))

    def render(self) -> str:
        if not self.changes:
            return "No drift: the file matches its sources."
        lines = [c.render() for c in self.changes]
        drift = sum(1 for c in self.changes if c.kind not in _NOT_DRIFT)
        kept = sum(1 for c in self.changes if c.kind == "kept_yours")
        warned = len(self.warnings)
        if drift == 0 and kept == 0:
            return "\n".join(lines + ["", f"No drift; {warned} warning(s)."])
        summary = f"{drift} change(s) from the sources"
        if kept:
            summary += f", {kept} line(s) of yours kept over the sources"
        if warned:
            summary += f", {warned} warning(s)"
        return "\n".join(lines + ["", summary + "."])


def _has_yours(statement: Statement) -> bool:
    """True if a property line holds child lines written by a person (or a proposal)."""
    return any(not c.is_auto for c in statement.children)


def _merge_children(old: list[Statement], new: list[Statement], where: str, report: DriftReport) -> list[Statement]:
    return _merge_lines(old, new, where, report, renames=False)


def _merge_lines(old: list[Statement], new: list[Statement], where: str, report: DriftReport,
                 renames: bool = True) -> list[Statement]:
    yours = [s for s in old if not s.is_auto]
    your_keys = {s.key() for s in yours if s.kind != "comment"}
    fresh: dict[tuple, Statement] = {}
    for s in new:
        fresh.setdefault(s.key(), s)
    old_keys = {s.key() for s in old}

    # Property renames: a vanished extracted property holding your child lines,
    # and exactly one new extracted property of the same kind and type.
    renamed: dict[tuple, Statement] = {}  # new key -> old statement
    if renames:
        added = [s for k, s in fresh.items() if s.is_property and k not in old_keys and k not in your_keys]
        for s in old:
            if not (s.is_auto and s.is_property) or s.key() in fresh or not _has_yours(s):
                continue
            matches = [a for a in added if a.kind == s.kind and a.target == s.target and a.key() not in renamed]
            if len(matches) == 1:
                renamed[matches[0].key()] = s
    renamed_old = {id(s) for s in renamed.values()}

    merged: list[Statement] = []
    placed: set[tuple] = set()
    for s in old:
        key = s.key()
        if not s.is_auto:
            mine = copy.deepcopy(s)
            if s.is_property:
                match = fresh.get(key)
                mine.children = _merge_children(s.children, match.children if match else [], f"{where}.{s.name}", report)
            merged.append(mine)
            continue
        if key in your_keys or key in placed or id(s) in renamed_old:
            continue
        replacement = fresh.get(key)
        if replacement is None:
            if s.is_property and _has_yours(s):
                kept = copy.deepcopy(s)
                kept.tag = None
                kept.children = [c for c in kept.children if not c.is_auto]
                merged.append(kept)
                report.add("property_kept", where, s.body())
            else:
                report.add("removed", where, s.body())
            continue
        if replacement.render() != s.render():
            report.add("changed", where, replacement.body(), s.body())
        line = copy.deepcopy(replacement)
        if s.is_property:
            line.children = _merge_children(s.children, replacement.children, f"{where}.{s.name}", report)
        merged.append(line)
        placed.add(key)

    for key, s in fresh.items():
        if key in placed:
            continue
        if key in your_keys:
            mine = next(m for m in yours if m.key() == key)
            if mine.body() != s.body():
                report.add("kept_yours", where, mine.body(), s.body())
            continue
        line = copy.deepcopy(s)
        if key in renamed:
            before = renamed[key]
            line.children = _merge_children(before.children, s.children, f"{where}.{s.name}", report)
            report.add("property_renamed", where, s.name, before.name)
        else:
            report.add("added", where, s.body())
        merged.append(line)
        placed.add(key)
    return merged


def _prop_names(concept: Concept) -> set[str]:
    return {s.name for s in concept.properties() if s.is_auto}


def _holds_yours(concept: Concept) -> bool:
    return bool(concept.leading_comments) or any(not s.is_auto or (s.is_property and _has_yours(s)) for s in concept.statements)


def _concept_renames(current: Ontology, fresh: Ontology) -> dict[str, str]:
    """Map old concept name -> new concept name for likely renames."""
    gone = [c for n, c in current.concepts.items() if n not in fresh.concepts and c.tag is not None
            and c.tag.kind == "auto" and _holds_yours(c)]
    new = [c for n, c in fresh.concepts.items() if n not in current.concepts]
    renames: dict[str, str] = {}
    taken: set[str] = set()
    for old in gone:
        old_props = _prop_names(old)
        scored = []
        for cand in new:
            if cand.name in taken:
                continue
            cand_props = {s.name for s in cand.properties()}
            union = old_props | cand_props
            shared = old_props & cand_props
            if union and len(shared) >= 2 and len(shared) / len(union) >= RENAME_SIMILARITY:
                scored.append((len(shared) / len(union), cand.name))
        scored.sort(reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            renames[old.name] = scored[0][1]
            taken.add(scored[0][1])
    return renames


def merge(current: Ontology, fresh: Ontology, renames: bool = True) -> tuple[Ontology, DriftReport]:
    """Return the merged ontology and what changed. Neither input is modified.

    With ``renames=False`` nothing is treated as a rename: a renamed table is
    reported as one concept gone (kept if it holds your lines) and one added.
    """
    out = Ontology(
        comments=list(current.comments),
        prefixes=dict(current.prefixes),
        sources=list(current.sources),
        trailing_comments=list(current.trailing_comments),
    )
    for prefix, iri in fresh.prefixes.items():
        out.prefixes.setdefault(prefix, iri)
    report = DriftReport()
    for concept, message in fresh.warnings:
        report.add("warning", concept, message)
    detect_renames = renames
    renames = _concept_renames(current, fresh) if detect_renames else {}
    renamed_to = set(renames.values())

    order: list[tuple[str, str]] = []  # (old name or "", new name)
    for name in current.concepts:
        order.append((name, renames.get(name, name)))
    order += [("", n) for n in fresh.concepts if n not in current.concepts and n not in renamed_to]

    for old_name, name in order:
        old = current.concepts.get(old_name) if old_name else None
        new = fresh.concepts.get(name)
        if old is None:
            out.concepts[name] = copy.deepcopy(new)
            report.add("concept_added", name, new.header())
            continue
        if old_name != name:
            report.add("concept_renamed", name, new.header(), old_name)

        if new is None:
            yours = [s for s in old.statements if not s.is_auto or (s.is_property and _has_yours(s))]
            if old.tag is None or old.tag.kind != "auto":
                kept = copy.deepcopy(old)
                kept.statements = _merge_lines(old.statements, [], name, report, renames=False)
                out.concepts[name] = kept
                continue
            if not yours and not old.leading_comments:
                report.add("concept_removed", name, old.header())
                continue
            silent = DriftReport()
            statements = _merge_lines(old.statements, [], name, silent, renames=False)
            out.concepts[name] = Concept(name, None, statements, list(old.leading_comments))
            report.add("concept_kept", name, old.header())
            continue

        statements = _merge_lines(old.statements, new.statements, name, report, renames=detect_renames)
        out.concepts[name] = Concept(name, new.tag, statements, list(old.leading_comments))

    return out, report

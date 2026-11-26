"""Merge a fresh extraction into an existing file and report drift.

Rules (see SPEC.md, "Refresh rules"):

1. Lines without a tag, and ``[proposed]`` lines, belong to people. They are
   always kept, in place.
2. ``[auto]`` lines are replaced by what the sources say now. Auto lines the
   sources no longer produce are removed.
3. If a person's line has the same key as an extracted line (for example both
   describe the property ``title``), the person's line wins and the extracted
   one is not written.
4. A concept that disappears from the sources is removed only if it was
   extracted and holds no lines of yours. Otherwise it is kept and reported.
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
}


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
        if self.kind == "changed":
            return f"{sym} {self.concept}: {self.line}  (was: {self.previous})"
        if self.kind == "kept_yours":
            return f"{sym} {self.concept}: kept your line {self.line}  (sources say: {self.previous})"
        return f"{sym} {self.concept}: {self.line}"


@dataclass
class DriftReport:
    changes: list[Change] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return any(c.kind != "kept_yours" for c in self.changes)

    def render(self) -> str:
        if not self.changes:
            return "No drift: the file matches its sources."
        lines = [c.render() for c in self.changes]
        drift = sum(1 for c in self.changes if c.kind != "kept_yours")
        kept = len(self.changes) - drift
        summary = f"{drift} change(s) from the sources"
        if kept:
            summary += f", {kept} line(s) of yours kept over the sources"
        return "\n".join(lines + ["", summary + "."])


def _is_people_owned(concept: Concept) -> bool:
    return concept.tag is None or concept.tag.kind != "auto"


def merge(current: Ontology, fresh: Ontology) -> tuple[Ontology, DriftReport]:
    """Return the merged ontology and what changed. Neither input is modified."""
    out = Ontology(
        comments=list(current.comments),
        prefixes=dict(current.prefixes),
        sources=list(current.sources),
        trailing_comments=list(current.trailing_comments),
    )
    for prefix, iri in fresh.prefixes.items():
        out.prefixes.setdefault(prefix, iri)
    report = DriftReport()
    names = list(current.concepts) + [n for n in fresh.concepts if n not in current.concepts]

    for name in names:
        old = current.concepts.get(name)
        new = fresh.concepts.get(name)
        if old is None:
            out.concepts[name] = copy.deepcopy(new)
            report.changes.append(Change("concept_added", name, new.header()))
            continue

        yours = [s for s in old.statements if not s.is_auto]
        your_keys = {s.key() for s in yours if s.kind != "comment"}

        if new is None:
            if _is_people_owned(old):
                out.concepts[name] = copy.deepcopy(old)
                out.concepts[name].statements = copy.deepcopy(yours)
                report.changes.extend(Change("removed", name, s.body()) for s in old.statements if s.is_auto)
                continue
            if not yours and not old.leading_comments:
                report.changes.append(Change("concept_removed", name, old.header()))
                continue
            kept = Concept(name, None, list(yours), list(old.leading_comments))
            out.concepts[name] = kept
            report.changes.append(Change("concept_kept", name, old.header()))
            continue

        fresh_auto: dict[tuple, Statement] = {}
        for s in new.statements:
            fresh_auto.setdefault(s.key(), s)

        merged: list[Statement] = []
        placed: set[tuple] = set()
        for s in old.statements:
            if not s.is_auto:
                merged.append(s)
                continue
            key = s.key()
            if key in your_keys or key in placed:
                continue
            replacement = fresh_auto.get(key)
            if replacement is None:
                report.changes.append(Change("removed", name, s.body()))
                continue
            if replacement.render() != s.render():
                report.changes.append(Change("changed", name, replacement.body(), s.body()))
            merged.append(replacement)
            placed.add(key)

        for key, s in fresh_auto.items():
            if key in placed:
                continue
            if key in your_keys:
                mine = next(m for m in yours if m.key() == key)
                if mine.body() != s.body():
                    report.changes.append(Change("kept_yours", name, mine.body(), s.body()))
                continue
            merged.append(s)
            placed.add(key)
            report.changes.append(Change("added", name, s.body()))

        out.concepts[name] = Concept(name, new.tag, copy.deepcopy(merged), list(old.leading_comments))

    return out, report

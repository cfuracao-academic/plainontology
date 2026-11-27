"""Merge a fresh extraction into an existing file and report drift.

Rules (see SPEC.md, "Refresh rules"):

1. Lines without a tag, and ``[proposed]`` lines, belong to people. They are
   always kept, in place.
2. ``[auto]`` lines are replaced by what the sources say now. Auto lines the
   sources no longer produce are removed.
3. If a person's line has the same key as an extracted line (for example both
   describe the property ``title``), the person's line wins and the extracted
   one is not written. A ``[proposed]`` line is the exception: once the sources
   produce its key, the extracted line replaces it (the proposal came true).
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
    "overridden": "-",
    "proposal_confirmed": "~",
}

# Report lines that are not drift: nothing about the sources changed.
_NOT_DRIFT = ("kept_yours", "warning")

RENAME_SIMILARITY = 0.6

# Shown under any ? warnings: what a warning means and where the scope is described.
SCOPE_HINT = '? warnings mean something in the source was not fully read: see "Scope" in the README'


def _printable(text: str) -> str:
    """Report lines never contain a line break or control character (table names
    can), so a report can be put into CI outputs and comments safely."""
    out = []
    for ch in text:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif not ch.isprintable() and ch != " ":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


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
            hint = f"; {self.previous}" if self.previous else ""
            return f"{sym} {self.concept}: {self.line} is gone from the sources; kept because it holds your lines{hint}"
        if self.kind == "concept_renamed":
            return f"{sym} concept {self.previous} renamed to {self.concept}; your lines moved with it"
        if self.kind == "property_renamed":
            return f"{sym} {self.concept}: {self.previous} renamed to {self.line}; your lines moved with it"
        if self.kind == "changed":
            return f"{sym} {self.concept}: {self.line}  (was: {self.previous})"
        if self.kind == "kept_yours":
            return f"{sym} {self.concept}: kept your line {self.line}  (sources say: {self.previous})"
        if self.kind == "overridden":
            return f"{sym} {self.concept}: {self.line}  (replaced by your line {self.previous})"
        if self.kind == "proposal_confirmed":
            return (
                f"{sym} {self.concept}: {self.line}  (now in the sources; replaces the proposed line {self.previous})"
            )
        if self.kind == "warning":
            subject = f"concept {self.concept} is " if self.concept else ""
            return f"{sym} warning: {subject}{self.line}"
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
        lines = [_printable(c.render()) for c in self.changes]
        drift = sum(1 for c in self.changes if c.kind not in _NOT_DRIFT)
        kept = sum(1 for c in self.changes if c.kind == "kept_yours")
        warned = len(self.warnings)
        if drift == 0 and kept == 0:
            return "\n".join([*lines, "", f"No drift; {warned} warning(s).", SCOPE_HINT])
        summary = f"{drift} change(s) from the sources"
        if kept:
            summary += f", {kept} line(s) of yours kept over the sources"
        if warned:
            summary += f", {warned} warning(s)"
        return "\n".join([*lines, "", summary + "."] + ([SCOPE_HINT] if warned else []))


def _is_proposed(statement: Statement) -> bool:
    return statement.tag is not None and statement.tag.kind == "proposed"


def _has_yours(statement: Statement) -> bool:
    """True if a property line holds child lines written by a person (or a proposal)."""
    return any(not c.is_auto for c in statement.children)


def _merge_children(old: list[Statement], new: list[Statement], where: str, report: DriftReport) -> list[Statement]:
    return _merge_lines(old, new, where, report, renames=False)


def _unique_pairs(scores: list[tuple[float, str, str]]) -> dict[str, str]:
    """Pairs (a, b) where b is a's single best match and a is b's single best
    match. Anything tied or contested is left out: reported, not guessed."""
    best_for_a: dict[str, list[tuple[float, str]]] = {}
    best_for_b: dict[str, list[tuple[float, str]]] = {}
    for score, a, b in scores:
        best_for_a.setdefault(a, []).append((score, b))
        best_for_b.setdefault(b, []).append((score, a))

    def single_best(options: list[tuple[float, str]]) -> str | None:
        options = sorted(options, reverse=True)
        if len(options) > 1 and options[0][0] == options[1][0]:
            return None
        return options[0][1]

    pairs = {}
    for a, options in best_for_a.items():
        chosen = single_best(options)
        if chosen is not None and single_best(best_for_b[chosen]) == a and len(best_for_b[chosen]) == 1:
            pairs[a] = chosen
    return pairs


def _merge_lines(
    old: list[Statement],
    new: list[Statement],
    where: str,
    report: DriftReport,
    renames: bool = True,
    target_map: dict[str, str] | None = None,
) -> list[Statement]:
    fresh: dict[tuple, Statement] = {}
    for s in new:
        fresh.setdefault(s.key(), s)
    # A proposal the sources now produce gives way to the extracted line.
    person_keys = {s.key() for s in old if s.tag is None and s.kind != "comment"}
    confirmed = {s.key() for s in old if _is_proposed(s) and s.key() in fresh and s.key() not in person_keys}
    yours = [s for s in old if not s.is_auto and not (_is_proposed(s) and s.key() in confirmed)]
    your_keys = {s.key() for s in yours if s.kind != "comment"}
    old_keys = {s.key() for s in old}

    # Property renames: a vanished extracted property holding your child lines,
    # and exactly one new extracted property of the same kind and type.
    renamed: dict[tuple, Statement] = {}  # new key -> old statement
    if renames:
        targets = target_map or {}
        added = [s for k, s in fresh.items() if s.is_property and k not in old_keys and k not in your_keys]
        gone = [
            s
            for s in old
            if s.is_auto and s.is_property and s.key() not in fresh and s.key() not in your_keys and _has_yours(s)
        ]
        candidates = [
            (1.0, s.name, a.name)
            for s in gone
            for a in added
            if a.kind == s.kind and a.target == targets.get(s.target, s.target)
        ]
        by_name = {s.name: s for s in gone}
        for old_name, new_name in _unique_pairs(candidates).items():
            renamed[("prop", new_name)] = by_name[old_name]
    renamed_old = {id(s) for s in renamed.values()}
    new_key_of = {id(s): k for k, s in renamed.items()}

    # Lines of yours under an extracted line that you overrode with your own line
    # of the same key move to your line, so overriding never loses them.
    orphans: dict[tuple, list[Statement]] = {}
    for s in old:
        if s.is_auto and s.is_property and s.key() in your_keys:
            orphans.setdefault(s.key(), []).extend(c for c in s.children if not c.is_auto)

    merged: list[Statement] = []
    placed: set[tuple] = set()
    placed_line: dict[tuple, Statement] = {}

    def hand_over(s: Statement) -> None:
        """A line skipped because its key is already written: its lines of yours
        move under the written line, never dropped."""
        line = placed_line.get(s.key())
        yours_under = [c for c in s.children if not c.is_auto]
        if line is not None and yours_under:
            # Merged, not appended: a line of yours wins over an extracted one of the same key.
            match = fresh.get(s.key())
            line.children = _merge_children(
                [*line.children, *copy.deepcopy(yours_under)], match.children if match else [], where, DriftReport()
            )

    for s in old:
        key = s.key()
        if key in confirmed and _is_proposed(s):
            if key in placed:
                report.add("proposal_confirmed", where, placed_line[key].body(), s.body())
                hand_over(s)
                continue
            line = copy.deepcopy(fresh[key])
            line.blank_before = s.blank_before
            if s.is_property:
                line.children = _merge_children(s.children, line.children, f"{where}.{s.name}", report)
            report.add("proposal_confirmed", where, line.body(), s.body())
            merged.append(line)
            placed.add(key)
            placed_line[key] = line
            continue
        if not s.is_auto:
            mine = copy.deepcopy(s)
            if s.is_property:
                match = fresh.get(key)
                handed = copy.deepcopy(orphans.pop(key, []))  # from the extracted line you overrode
                mine.children = _merge_children(
                    [*s.children, *handed], match.children if match else [], f"{where}.{s.name}", report
                )
            merged.append(mine)
            continue
        if key in your_keys:
            # Your line of the same key wins; the extracted line leaves the file.
            mine = next(m for m in yours if m.key() == key)
            report.add("overridden", where, s.body(), mine.body())
            continue
        if key in placed:
            hand_over(s)
            continue
        if id(s) in renamed_old:
            # The renamed property is written where the old one was, next to anything above it.
            new_key = new_key_of[id(s)]
            line = copy.deepcopy(fresh[new_key])
            line.blank_before = s.blank_before
            line.children = _merge_children(s.children, fresh[new_key].children, f"{where}.{line.name}", report)
            report.add("property_renamed", where, line.name, s.name)
            merged.append(line)
            placed.add(new_key)
            placed_line[new_key] = line
            continue
        replacement = fresh.get(key)
        if replacement is None:
            if s.is_property and _has_yours(s):
                # Kept, still tagged [auto], and reported on every run until a person
                # moves or deletes their lines: CI keeps flagging a property whose
                # source is gone, and it refreshes normally if the source comes back.
                kept = copy.deepcopy(s)
                kept.children = [c for c in kept.children if not c.is_auto]
                merged.append(kept)
                maybe = [
                    a.name
                    for k, a in fresh.items()
                    if a.is_property
                    and k not in old_keys
                    and a.kind == s.kind
                    and a.target == (target_map or {}).get(s.target, s.target)
                ]
                if maybe:
                    hint = (
                        f"if it was renamed to {' or '.join(maybe)}, run: "
                        f"plainonto rename FILE {where}.{s.name} {maybe[0]}"
                    )
                else:
                    hint = f"if it was renamed, run: plainonto rename FILE {where}.{s.name} NEW_NAME"
                report.add("property_kept", where, s.body(), hint)
            else:
                report.add("removed", where, s.body())
            continue
        if replacement.render() != s.render():
            report.add("changed", where, replacement.body(), s.body())
        line = copy.deepcopy(replacement)
        line.blank_before = s.blank_before
        if s.is_property:
            line.children = _merge_children(s.children, replacement.children, f"{where}.{s.name}", report)
        merged.append(line)
        placed.add(key)
        placed_line[key] = line

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
    return bool(concept.leading_comments) or any(
        not s.is_auto or (s.is_property and _has_yours(s)) for s in concept.statements
    )


def _concept_renames(current: Ontology, fresh: Ontology, only_yours: bool = True) -> dict[str, str]:
    """Map old concept name -> new concept name for clear renames only. With
    ``only_yours`` just concepts holding lines of yours (the ones whose lines move);
    without it every extracted concept (to follow relations to renamed targets)."""
    gone = [
        c
        for n, c in current.concepts.items()
        if n not in fresh.concepts
        and c.tag is not None
        and c.tag.kind == "auto"
        and (_holds_yours(c) or not only_yours)
    ]
    new = [c for n, c in fresh.concepts.items() if n not in current.concepts]
    scores: list[tuple[float, str, str]] = []
    for old in gone:
        old_props = _prop_names(old)
        for cand in new:
            cand_props = {s.name for s in cand.properties()}
            union = old_props | cand_props
            shared = old_props & cand_props
            if union and len(shared) >= 2 and len(shared) / len(union) >= RENAME_SIMILARITY:
                scores.append((len(shared) / len(union), old.name, cand.name))
    return _unique_pairs(scores)


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
        preamble=list(current.preamble),
        from_file=current.from_file,
        newline=current.newline,
        trailing_blank_before=current.trailing_blank_before,
        trailing_blanks=current.trailing_blanks,
        final_newline=current.final_newline,
        leading_blanks=current.leading_blanks,
        bom=current.bom,
    )
    for prefix, iri in fresh.prefixes.items():
        out.prefixes.setdefault(prefix, iri)
    report = DriftReport()
    for concept, message in fresh.warnings:
        report.add("warning", concept, message)
    detect_renames = renames
    rename_map = _concept_renames(current, fresh) if detect_renames else {}
    target_map = _concept_renames(current, fresh, only_yours=False) if detect_renames else {}
    renamed_to = set(rename_map.values())

    order: list[tuple[str, str]] = []  # (old name or "", new name)
    for name in current.concepts:
        order.append((name, rename_map.get(name, name)))
    order += [("", n) for n in fresh.concepts if n not in current.concepts and n not in renamed_to]

    for old_name, name in order:
        old = current.concepts.get(old_name) if old_name else None
        new = fresh.concepts.get(name)
        if old is None:
            assert new is not None  # only names from the sources reach here without an old concept
            out.concepts[name] = copy.deepcopy(new)
            report.add("concept_added", name, new.header())
            continue
        if old_name != name and new is not None:
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
            out.concepts[name] = Concept(name, old.tag, statements, list(old.leading_comments), old.raw_header)
            report.add("concept_kept", name, old.header())
            continue

        statements = _merge_lines(
            old.statements, new.statements, name, report, renames=detect_renames, target_map=target_map
        )
        if old.tag is None or old.tag.kind != "auto":
            # A person's (or a proposed) concept stays theirs; extracted lines still refresh inside it.
            out.concepts[name] = Concept(name, old.tag, statements, list(old.leading_comments), old.raw_header)
            if old.tag is not None and old.tag.kind == "proposed":
                report.add(
                    "warning", name, "still [proposed], but the sources now produce it; delete the tag to accept it"
                )
        else:
            if old.tag != new.tag and old_name == name:  # e.g. the table behind it was renamed
                report.add("changed", name, new.header(), old.header())
            # H1 (fifth review): a renamed concept gets a fresh header, never the old name's text.
            header = old.raw_header if old_name == name else ""
            out.concepts[name] = Concept(name, new.tag, statements, list(old.leading_comments), header)

    for old_name, name in order:  # keep the blank lines above each concept
        if old_name and name in out.concepts:
            out.concepts[name].blank_before = current.concepts[old_name].blank_before

    # M3: lines of yours that still point to a renamed concept are reported, never rewritten.
    for old_name, new_name in {**target_map, **rename_map}.items():
        for holder in out.concepts.values():
            for s in holder.statements:
                if s.is_auto:
                    continue
                if (s.kind == "rel" and s.target == old_name) or (s.kind == "is_a" and s.name == old_name):
                    report.add(
                        "warning",
                        "",
                        f"{holder.name}: your line '{s.body()}' still points to {old_name}, now {new_name}; "
                        f"to update it run: plainonto rename FILE {old_name} {new_name}",
                    )

    return out, report

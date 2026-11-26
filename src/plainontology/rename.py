"""Rename a concept or a property everywhere in a file, by hand.

Refresh recognises most renames in the sources by itself (see merge.py). Use
this when it did not: run it before or after refreshing, with the old and the
new name, and your lines follow.

Renaming onto a name that already exists merges the two. No line of yours is
ever dropped: your lines move under the new name, and if one clashes with a
line already there, both are kept and ``plainonto check`` points them out.
Extracted ``[auto]`` lines of the old name are dropped, because the sources
now describe the new name.

To give an extracted concept a friendlier name, add a ``label`` instead: the
concept name follows the source.
"""

from __future__ import annotations

import re

from .model import Concept, Ontology, Statement, unique_columns
from .textformat import NAME

_NAME = re.compile(rf"^{NAME}$")


def _is_yours(s: Statement) -> bool:
    return not s.is_auto


def _append(lines: list[Statement], into: list[Statement]) -> tuple[int, int]:
    """Append every line; return (moved, clashes with keys already present)."""
    keys = {s.key() for s in into if s.kind != "comment"}
    moved = clashes = 0
    for s in list(lines):
        if s.kind != "comment" and s.key() in keys:
            clashes += 1
        into.append(s)
        moved += 1
    return moved, clashes


def _update_references(onto: Ontology, old: str, new: str) -> int:
    refs = 0
    for c in onto.concepts.values():
        for s in c.statements:
            if s.kind == "rel" and s.target == old:
                s.target, s.raw, refs = new, "", refs + 1
            elif s.kind == "is_a" and s.name == old:
                s.name, s.raw, refs = new, "", refs + 1
    return refs


def _merge_concepts(source: Concept, target: Concept) -> tuple[int, int, int]:
    """Move every line of yours from ``source`` into ``target``. Returns
    (lines moved, clashes, extracted lines dropped)."""
    moved = clashes = dropped = 0
    for s in list(source.statements):
        if s.is_property and s.is_auto:
            yours = [c for c in s.children if _is_yours(c)]
            if not yours:
                dropped += 1
                continue
            existing = target.prop(s.name)
            if existing is not None:
                m, c = _append(yours, existing.children)
                moved, clashes, dropped = moved + m, clashes + c, dropped + 1
                continue
            s.children = yours
            m, c = _append([s], target.statements)
            moved, clashes = moved + m, clashes + c
            continue
        if s.is_auto:
            dropped += 1
            continue
        m, c = _append([s], target.statements)
        moved, clashes = moved + m, clashes + c
    target.leading_comments += source.leading_comments
    return moved, clashes, dropped


def rename_concept(onto: Ontology, old: str, new: str) -> str:
    if old == new:
        raise ValueError(f"{old} and {new} are the same name; nothing to do")
    if not _NAME.match(new):
        raise ValueError(f"'{new}' is not a valid concept name")
    if old not in onto.concepts:
        # The concept was already renamed by a refresh: update the references left behind.
        refs = _update_references(onto, old, new)
        if not refs:
            raise ValueError(f"no concept named {old}, and no line points to it")
        return f"Updated {refs} reference(s) from {old} to {new}."
    concept = onto.concepts[old]
    refs = _update_references(onto, old, new)
    if new in onto.concepts:
        moved, clashes, dropped = _merge_concepts(concept, onto.concepts[new])
        del onto.concepts[old]
        message = f"Merged {old} into {new}: {moved} line(s) of yours moved"
        if clashes:
            message += f", {clashes} of them clash with lines already there (both kept; see plainonto check)"
        if dropped:
            message += f", {dropped} extracted line(s) of {old} dropped"
        return message + f", {refs} reference(s) updated."
    onto.concepts = {(new if n == old else n): c for n, c in onto.concepts.items()}
    concept.name, concept.raw_header = new, ""
    return f"Renamed {old} to {new}: {refs} reference(s) updated."


def rename_property(onto: Ontology, concept_name: str, old: str, new: str) -> str:
    if old == new:
        raise ValueError(f"{concept_name}.{old} and {concept_name}.{new} are the same name; nothing to do")
    concept: Concept | None = onto.concepts.get(concept_name)
    if concept is None:
        raise ValueError(f"no concept named {concept_name}")
    if not _NAME.match(new) or "." in new:
        raise ValueError(f"'{new}' is not a valid property name")
    prop = concept.prop(old)
    if prop is None:
        raise ValueError(f"{concept_name} has no property {old}")
    existing = concept.prop(new)
    if existing is None:
        prop.name, prop.raw = new, ""
        for s in concept.all("unique"):
            cols = [new if c == old else c for c in unique_columns(s.name)]
            s.name, s.raw = ", ".join(cols), ""
        return f"Renamed {concept_name}.{old} to {concept_name}.{new}."
    if _is_yours(prop) and _is_yours(existing):
        raise ValueError(f"{concept_name}.{old} and {concept_name}.{new} are both lines of yours; merge them by hand")
    if any(old in unique_columns(s.name) and new in unique_columns(s.name) for s in concept.all("unique")):
        raise ValueError(f"{old} and {new} are in the same unique (...) line; merge them by hand")
    keep, drop = (prop, existing) if _is_yours(prop) else (existing, prop)
    if keep is prop:
        prop.name, prop.raw = new, ""
    moved, clashes = _append([c for c in drop.children if _is_yours(c)], keep.children)
    concept.statements.remove(drop)
    for s in concept.all("unique"):
        cols = [new if c == old else c for c in unique_columns(s.name)]
        s.name, s.raw = ", ".join(cols), ""
    message = f"Merged {concept_name}.{old} into {concept_name}.{new}: {moved} line(s) of yours moved"
    if clashes:
        message += f", {clashes} of them clash with lines already there (both kept; see plainonto check)"
    return message + "."


def rename(onto: Ontology, old: str, new: str) -> str:
    """``old`` and ``new`` are concept names, or ``Concept.property`` for a property."""
    if "." in old and old.split(".", 1)[0] in onto.concepts:
        concept, prop = old.split(".", 1)
        if "." in new and not new.startswith(concept + "."):
            raise ValueError(
                "a property can only be renamed within its concept (moving it to another is not supported)"
            )
        new_prop = new.split(".", 1)[1] if new.startswith(concept + ".") else new
        return rename_property(onto, concept, prop, new_prop)
    return rename_concept(onto, old, new)

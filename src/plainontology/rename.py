"""Rename a concept or a property everywhere in a file, by hand.

Refresh recognises most renames in the sources by itself (see merge.py). Use
this when it did not: run it before refreshing, with the old and the new name,
and your lines follow. Renaming onto a name that already exists merges the two:
the lines of the old one move under the new one, skipping lines already there.

To give an extracted concept a friendlier name, add a ``label`` instead: the
concept name follows the source.
"""

from __future__ import annotations

import re

from .model import Concept, Ontology, Statement, unique_columns

_NAME = re.compile(r"^[^\W\d][\w.:/-]*$")


def _move(lines: list[Statement], into: list[Statement]) -> int:
    keys = {s.key() for s in into}
    moved = 0
    for s in lines:
        if s.kind == "comment" or s.key() not in keys:
            into.append(s)
            keys.add(s.key())
            moved += 1
    return moved


def rename_concept(onto: Ontology, old: str, new: str) -> str:
    if not _NAME.match(new):
        raise ValueError(f"'{new}' is not a valid concept name")
    refs = 0
    if old not in onto.concepts:
        # The concept was already renamed by a refresh: update the references left behind.
        for c in onto.concepts.values():
            for s in c.statements:
                if s.kind == "rel" and s.target == old:
                    s.target, s.raw, refs = new, "", refs + 1
                elif s.kind == "is_a" and s.name == old:
                    s.name, s.raw, refs = new, "", refs + 1
        if not refs:
            raise ValueError(f"no concept named {old}, and no line points to it")
        return f"Updated {refs} reference(s) from {old} to {new}."
    concept = onto.concepts[old]
    for c in onto.concepts.values():
        for s in c.statements:
            if s.kind == "rel" and s.target == old:
                s.target, s.raw, refs = new, "", refs + 1
            elif s.kind == "is_a" and s.name == old:
                s.name, s.raw, refs = new, "", refs + 1
    if new in onto.concepts:
        target = onto.concepts[new]
        moved = _move(concept.statements, target.statements)
        target.leading_comments += concept.leading_comments
        del onto.concepts[old]
        return f"Merged {old} into {new}: {moved} line(s) moved, {refs} reference(s) updated."
    onto.concepts = {(new if n == old else n): c for n, c in onto.concepts.items()}
    concept.name, concept.raw_header = new, ""
    return f"Renamed {old} to {new}: {refs} reference(s) updated."


def rename_property(onto: Ontology, concept_name: str, old: str, new: str) -> str:
    concept: Concept | None = onto.concepts.get(concept_name)
    if concept is None:
        raise ValueError(f"no concept named {concept_name}")
    if not _NAME.match(new):
        raise ValueError(f"'{new}' is not a valid property name")
    prop = concept.prop(old)
    if prop is None:
        raise ValueError(f"{concept_name} has no property {old}")
    existing = concept.prop(new)
    if existing is not None:
        moved = _move(prop.children, existing.children)
        concept.statements.remove(prop)
        message = f"Merged {concept_name}.{old} into {concept_name}.{new}: {moved} line(s) moved."
    else:
        prop.name, prop.raw = new, ""
        message = f"Renamed {concept_name}.{old} to {concept_name}.{new}."
    for s in concept.all("unique"):
        cols = [new if c == old else c for c in unique_columns(s.name)]
        s.name, s.raw = ", ".join(dict.fromkeys(cols)), ""
    return message


def rename(onto: Ontology, old: str, new: str) -> str:
    """``old`` and ``new`` are concept names, or ``Concept.property`` for a property."""
    if "." in old and old.split(".", 1)[0] in onto.concepts:
        concept, prop = old.split(".", 1)
        new_prop = new.split(".", 1)[1] if new.startswith(concept + ".") else new
        return rename_property(onto, concept, prop, new_prop)
    return rename_concept(onto, old, new)

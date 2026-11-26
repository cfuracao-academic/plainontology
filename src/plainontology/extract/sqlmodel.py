"""A dialect-neutral view of relational tables, and how it becomes concepts.

Every SQL extractor (SQLite, PostgreSQL and MySQL; dump files and live
databases) reads its source into ``Table`` objects and hands them to
``tables_to_concepts``, so all of them follow the same rules:

* a table becomes a concept (``books`` becomes ``Book``)
* a column becomes a ``has`` line; a single-column primary key is ``key``
* a foreign key becomes a relation (``author_id`` becomes ``author -> Author``)
* a join table (two single-column foreign keys and nothing else) becomes a
  ``many`` relation on the first table instead of a concept of its own
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import AUTO, Concept, Statement, Tag
from ..naming import concept_name, plural, relation_name, safe_name


@dataclass
class Column:
    name: str
    datatype: str  # a PlainOntology datatype, or enum(a|b)
    not_null: bool = False
    many: bool = False  # array column


@dataclass
class ForeignKey:
    columns: list[str]
    table: str


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    unique: set[str] = field(default_factory=set)  # single-column unique constraints

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


def enum_type(values: list[str]) -> str:
    cleaned = [re.sub(r"[|()]", "_", str(v)).strip() or "_" for v in values]
    return f"enum({'|'.join(cleaned)})" if cleaned else "text"


def map_type(declared: str) -> str:
    """Map a declared SQL type (any dialect) to a PlainOntology datatype."""
    d = (declared or "").upper().strip()
    if not d:
        return "any"
    base = re.sub(r"\(.*", "", d).strip()
    if d.startswith("TINYINT(1)") or "BOOL" in d or d in ("BIT", "BIT(1)"):
        return "boolean"
    if base.startswith(("INTERVAL", "TIME ", "TIMETZ")) or base in ("TIME", "POINT", "MULTIPOINT"):
        return "text"
    if "DATETIME" in d or "TIMESTAMP" in d:
        return "datetime"
    if base == "DATE":
        return "date"
    if "JSON" in d:
        return "json"
    if any(x in d for x in ("BLOB", "BYTEA", "BINARY")):
        return "binary"
    if "INT" in base or "SERIAL" in base or base in ("YEAR",):
        return "integer"
    if any(x in base for x in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL", "MONEY", "NUMBER")):
        return "decimal"
    return "text"


def tables_to_concepts(tables: list[Table], label: str) -> list[Concept]:
    """Turn tables into concepts. ``label`` names the source kind in tags."""
    by_name = {t.name: t for t in tables}
    concepts: dict[str, Concept] = {}
    join_tables: list[tuple[Table, ForeignKey, ForeignKey]] = []

    for t in tables:
        positions = {c.name: i for i, c in enumerate(t.columns)}
        fks = sorted(t.foreign_keys, key=lambda fk: min(positions.get(c, 0) for c in fk.columns))
        fk_cols = {c for fk in fks for c in fk.columns}
        names = {c.name for c in t.columns}
        if len(fks) == 2 and names == fk_cols and all(len(fk.columns) == 1 for fk in fks):
            join_tables.append((t, fks[0], fks[1]))
            continue

        concept = Concept(concept_name(t.name), Tag("auto", f"{label} table {t.name}"))
        single_key = len(t.primary_key) == 1
        for col in t.columns:
            if col.name in fk_cols:
                continue
            flags = []
            if single_key and col.name in t.primary_key:
                flags.append("key")
            elif col.not_null or col.name in t.primary_key:
                flags.append("required")
            if col.name in t.unique and "key" not in flags:
                flags.append("unique")
            if col.many:
                flags.append("many")
            concept.add(Statement("has", name=safe_name(col.name), target=col.datatype, flags=tuple(flags), tag=AUTO))
        for fk in fks:
            target = concept_name(fk.table)
            if len(fk.columns) == 1:
                rel = relation_name(fk.columns[0])
            else:
                rel = target[:1].lower() + target[1:]
            required = all((c := t.column(name)) is not None and (c.not_null or name in t.primary_key) for name in fk.columns)
            detail = f"fk {t.name}.{','.join(fk.columns)}"
            concept.add(Statement("rel", name=rel, target=target, flags=("required",) if required else (), tag=Tag("auto", detail)))
        concepts[t.name] = concept

    for t, owner, other in join_tables:
        source = concepts.get(owner.table)
        if source is None or other.table not in by_name:
            continue
        name = plural(relation_name(other.columns[0]))
        source.add(Statement("rel", name=name, target=concept_name(other.table), flags=("many",), tag=Tag("auto", f"via {t.name}")))

    return list(concepts.values())

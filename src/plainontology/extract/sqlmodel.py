"""A dialect-neutral view of relational tables, and how it becomes concepts.

Every SQL extractor (SQLite, PostgreSQL and MySQL; dump files and live
databases) reads its source into ``Table`` objects and hands them to
``tables_to_concepts``, so all of them follow the same rules:

* a table or view becomes a concept (``books`` becomes ``Book``); tables in a
  non-default schema keep the schema (``archive.orders`` becomes ``ArchiveOrder``)
* a table comment becomes the concept's definition; a column comment becomes
  the property's description
* a column becomes a ``has`` line; a single-column primary key is ``key``;
  ``varchar(n)`` adds ``length ..n``
* CHECK constraints become enums or min/max/length rules where they can, and
  notes otherwise; triggers become notes
* a foreign key becomes a relation (``author_id`` becomes ``author -> Author``)
* multi-column unique constraints and primary keys become ``unique (a, b)``
* a join table (two single-column foreign keys forming its key) becomes a
  ``many`` relation on the first table; if it has other columns too, it also
  stays a concept so those columns are not lost
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
    comment: str = ""
    max_length: int | None = None
    pattern: str = ""


@dataclass
class ForeignKey:
    columns: list[str]
    table: str


@dataclass
class Table:
    name: str  # schema-qualified when not in the default schema
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    unique: set[str] = field(default_factory=set)  # single-column unique constraints
    unique_sets: list[list[str]] = field(default_factory=list)  # multi-column unique constraints
    comment: str = ""
    kind: str = "table"  # or "view"
    checks: list[str] = field(default_factory=list)  # CHECK expressions as SQL text
    triggers: list[str] = field(default_factory=list)  # e.g. "touch: BEFORE INSERT, UPDATE"

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


def enum_type(values: list[str]) -> str:
    cleaned = [re.sub(r"[|()]", "_", str(v)).strip() or "_" for v in values]
    return f"enum({'|'.join(cleaned)})" if cleaned else "text"


_LENGTH_RE = re.compile(r"^\s*(?:N?VARCHAR2?|N?CHAR|CHARACTER(?:\s+VARYING)?|VARYING\s+CHARACTER|NATIONAL\s+CHAR\w*)\s*\(\s*(\d+)\s*\)", re.I)


def declared_length(declared: str) -> int | None:
    """``varchar(13)`` gives 13; other types give None."""
    m = _LENGTH_RE.match(declared or "")
    return int(m[1]) if m else None


def map_type(declared: str) -> str:
    """Map a declared SQL type (any dialect) to a PlainOntology datatype."""
    d = (declared or "").upper().strip()
    if not d or d in ("NULLTYPE", "NULL"):
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


def qualified(schema: str | None, name: str, default_schemas: tuple[str, ...]) -> str:
    """``archive.orders`` for a non-default schema, plain ``orders`` otherwise."""
    if schema and schema.lower() not in default_schemas:
        return f"{schema}.{name}"
    return name


def _num(value) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _apply_checks(table: Table, dialect: str) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Fold CHECK constraints into column enums and rules; return rules and notes."""
    from .sqlcheck import analyse

    rules: dict[str, dict[str, str]] = {}
    notes: list[str] = []
    for sql in table.checks:
        for f in analyse(sql, dialect):
            col = table.column(f.column) if f.column else None
            if f.kind == "note" or col is None:
                notes.append(f"check: {f.value if f.kind == 'note' else sql}")
                continue
            if f.kind == "enum":
                if col.datatype in ("text", "any"):
                    col.datatype = enum_type(f.value)
                else:
                    notes.append(f"check: {sql}")
                continue
            value = f.value
            if f.exclusive:
                if col.datatype != "integer" and not f.kind.startswith("length"):
                    notes.append(f"check: {sql}")
                    continue
                value = value + 1 if f.kind in ("min", "length_min") else value - 1
            r = rules.setdefault(col.name, {})
            if f.kind in ("min", "max"):
                r[f.kind] = _num(value)
            elif f.kind == "length_max":
                col.max_length = min(int(value), col.max_length) if col.max_length else int(value)
            elif f.kind == "length_min":
                r["length_min"] = str(int(value))
            elif f.kind == "pattern":
                col.pattern = col.pattern or str(value)
    return rules, notes


def _children(col: Column, rules: dict[str, str]) -> list[Statement]:
    out = []
    if "min" in rules:
        out.append(Statement("min", value=rules["min"], tag=AUTO))
    if "max" in rules:
        out.append(Statement("max", value=rules["max"], tag=AUTO))
    low = rules.get("length_min", "")
    high = str(col.max_length) if col.max_length else ""
    if low or high:
        out.append(Statement("length", value=f"{low}..{high}", tag=AUTO))
    if col.pattern:
        out.append(Statement("pattern", value=col.pattern, tag=AUTO))
    return out


def tables_to_concepts(tables: list[Table], label: str) -> list[Concept]:
    """Turn tables into concepts. ``label`` names the source kind (and SQL dialect) in tags."""
    by_name = {t.name: t for t in tables}
    concepts: dict[str, Concept] = {}
    join_links: list[tuple[Table, ForeignKey, ForeignKey]] = []

    for t in tables:
        rules, notes = _apply_checks(t, label)
        positions = {c.name: i for i, c in enumerate(t.columns)}
        fks = sorted(t.foreign_keys, key=lambda fk: min(positions.get(c, 0) for c in fk.columns))
        fk_cols = {c for fk in fks for c in fk.columns}
        names = {c.name for c in t.columns}
        two_links = len(fks) == 2 and all(len(fk.columns) == 1 for fk in fks)
        keyed_by_links = set(t.primary_key) == fk_cols or any(set(u) == fk_cols for u in t.unique_sets)
        if two_links and names == fk_cols:
            join_links.append((t, fks[0], fks[1]))
            continue
        if two_links and keyed_by_links:
            join_links.append((t, fks[0], fks[1]))  # join table with data: also a concept

        kind = "view" if t.kind == "view" else "table"
        concept = Concept(concept_name(t.name), Tag("auto", f"{label} {kind} {t.name}"))
        if t.comment:
            concept.add(Statement("definition", value=t.comment, tag=AUTO))
        if t.kind == "view":
            concept.add(Statement("note", value="database view (read-only)", tag=AUTO))
        single_key = len(t.primary_key) == 1
        prop_name = {c.name: safe_name(c.name) for c in t.columns}
        for fk in fks:
            if len(fk.columns) == 1:
                prop_name[fk.columns[0]] = relation_name(fk.columns[0])
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
            line = Statement("has", name=prop_name[col.name], target=col.datatype, flags=tuple(flags),
                             value=col.comment, tag=AUTO)
            line.children = _children(col, rules.get(col.name, {}))
            concept.add(line)
        for fk in fks:
            target = concept_name(fk.table)
            rel = prop_name[fk.columns[0]] if len(fk.columns) == 1 else target[:1].lower() + target[1:]
            required = all((c := t.column(n)) is not None and (c.not_null or n in t.primary_key) for n in fk.columns)
            comment = t.column(fk.columns[0]).comment if len(fk.columns) == 1 and t.column(fk.columns[0]) else ""
            detail = f"fk {t.name}.{','.join(fk.columns)}"
            concept.add(Statement("rel", name=rel, target=target, flags=("required",) if required else (),
                                  value=comment, tag=Tag("auto", detail)))
        sets = [list(u) for u in t.unique_sets if len(u) > 1]
        if len(t.primary_key) > 1:
            sets.insert(0, list(t.primary_key))
        for cols in sets:
            concept.add(Statement("unique", name=", ".join(prop_name.get(c, safe_name(c)) for c in cols), tag=AUTO))
        for note in notes:
            concept.add(Statement("note", value=note, tag=AUTO))
        for trigger in t.triggers:
            concept.add(Statement("note", value=f"trigger {trigger}", tag=AUTO))
        concepts[t.name] = concept

    for t, owner, other in join_links:
        source = concepts.get(owner.table)
        if source is None or other.table not in by_name:
            continue
        name = plural(relation_name(other.columns[0]))
        source.add(Statement("rel", name=name, target=concept_name(other.table), flags=("many",),
                             tag=Tag("auto", f"via {t.name}")))

    return list(concepts.values())

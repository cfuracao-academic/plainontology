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

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal

from ..model import AUTO, Concept, Statement, Tag, make_enum, number_text
from ..naming import concept_name, pascal, plural, relation_name, safe_name, singular, words


@dataclass
class Column:
    name: str
    datatype: str  # a PlainOntology datatype, or enum(a|b)
    not_null: bool = False
    many: bool = False  # array column
    comment: str = ""
    max_length: int | None = None
    pattern: str = ""
    patterns: list[str] = field(default_factory=list)  # from CHECK constraints (all must match)
    unsigned: bool = False  # MySQL unsigned: min 0 (goes with the column through CHANGE and MODIFY)


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
    notes: list[str] = field(default_factory=list)  # e.g. "partitioned by RANGE (day) into 12 tables"
    inherits: list[str] = field(default_factory=list)  # PostgreSQL INHERITS (parent tables)

    def column(self, name: str) -> Column | None:
        """The column of that name; failing that, of that name in any case (column names
        are case-insensitive in PostgreSQL, unless quoted, and in MySQL)."""
        found = next((c for c in self.columns if c.name == name), None)
        if found is None:
            found = next((c for c in self.columns if c.name.lower() == name.lower()), None)
        return found


def fold_partition(parent: Table, child: Table) -> None:
    """Carry a partition's foreign keys up to its parent table (PostgreSQL lets
    them be declared on the partitions only)."""
    for fk in child.foreign_keys:
        if not any(f.columns == fk.columns and f.table == fk.table for f in parent.foreign_keys):
            parent.foreign_keys.append(ForeignKey(list(fk.columns), fk.table))


def enum_type(values: list, numbers: bool = False) -> str:
    return make_enum(values, numbers)


_LENGTH_RE = re.compile(
    r"^\s*(?:N?VARCHAR2?|N?CHAR|CHARACTER(?:\s+VARYING)?|VARYING\s+CHARACTER|NATIONAL\s+CHAR\w*)\s*\(\s*(\d+)\s*\)",
    re.I,
)


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
    return number_text(value)


_PG_REGEX = {r"\y": r"\b", r"\m": r"\b", r"\M": r"\b"}
# POSIX classes, which Python's re does not know and would silently misread.
_POSIX = {
    "alpha": "a-zA-Z\u00c0-\u024f",
    "alnum": "0-9a-zA-Z\u00c0-\u024f",
    "digit": "0-9",
    "upper": "A-Z\u00c0-\u00de",
    "lower": "a-z\u00df-\u00ff",
    "space": "\\s",
    "blank": " \\t",
    "punct": "!-/:-@\\[-`{-~",
    "xdigit": "0-9A-Fa-f",
    "word": "\\w",
    "cntrl": "\\x00-\\x1f\\x7f",
}


def python_pattern(pattern: str) -> str | None:
    """A database regex as a Python regex, or None when it cannot be used.
    PostgreSQL word boundaries (\\y, \\m, \\M) become \\b and POSIX classes
    such as [:alpha:] become explicit ranges."""
    import warnings

    for pg, py in _PG_REGEX.items():
        pattern = pattern.replace(pg, py)
    for name, ranges in _POSIX.items():
        pattern = pattern.replace(f"[:{name}:]", ranges)
    if "[:" in pattern and ":]" in pattern:
        return None  # a POSIX class we do not know
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # "possible nested set" means Python would read it differently
        try:
            re.compile(pattern)
        except (re.error, FutureWarning):
            return None
    return pattern


def _apply_checks(
    table: Table, dialect: str, link_columns: set[str] | frozenset[str] = frozenset()
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Fold CHECK constraints into column enums and rules; return rules and notes.
    A check on a foreign key column (``link_columns``) stays a note: the column is
    written as a relation, which has no rules."""
    from .sqlcheck import analyse

    rules: dict[str, dict[str, str]] = {}
    notes: list[str] = []
    for sql in table.checks:
        for f in analyse(sql, dialect):
            col = table.column(f.column) if f.column else None
            if col is not None and col.name in link_columns:
                if f"check: {sql}" not in notes:
                    notes.append(f"check: {sql}")
                continue
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
                lower = f.kind in ("min", "length_min")
                value = math.floor(value) + 1 if lower else math.ceil(value) - 1  # x > 2.5 on integers is x >= 3
            r = rules.setdefault(col.name, {})
            if f.kind == "min":  # several CHECKs: the strictest bound wins, compared exactly
                r["min"] = _num(max(Decimal(str(value)), Decimal(r["min"])) if "min" in r else value)
            elif f.kind == "max":
                r["max"] = _num(min(Decimal(str(value)), Decimal(r["max"])) if "max" in r else value)
            elif f.kind == "length_max":
                col.max_length = min(int(value), col.max_length) if col.max_length else int(value)
            elif f.kind == "length_min":
                r["length_min"] = str(max(int(value), int(r.get("length_min", 0))))
            elif f.kind == "pattern":
                usable = python_pattern(str(value))
                if usable is None:
                    notes.append(f"check: {sql} (the regex is not usable outside the database)")
                else:
                    if dialect == "mysql" and not usable.startswith("(?i)"):  # case-insensitive collations
                        usable = "(?i)" + usable
                    if usable not in col.patterns:
                        col.patterns.append(usable)
    return rules, notes


def _children(col: Column, rules: dict[str, str], notes: list[str]) -> list[Statement]:
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
        usable = python_pattern(col.pattern)
        if usable is None:
            notes.append(f"{col.name} must match {col.pattern} (the regex is not usable outside the database)")
        else:
            out.append(Statement("pattern", value=usable, tag=AUTO))
    for extra in col.patterns:
        if all(s.value != extra for s in out if s.kind == "pattern"):
            out.append(Statement("pattern", value=extra, tag=AUTO))
    return out


def _link_name(fk: ForeignKey) -> str:
    """The relation name for a join table link: from the column (book_id is book), or
    from the linked table when the column says nothing (Prisma's A and B)."""
    name = relation_name(fk.columns[0])
    if len(name.strip("_")) <= 1:
        name = safe_name("_".join(w.lower() for w in words(singular(fk.table.split(".")[-1]))))
    return name


def _concept_names(tables: list[Table], warnings: list[str] | None) -> dict[str, str]:
    names: dict[str, str] = {}
    taken: dict[str, str] = {}
    for t in tables:
        name = concept_name(t.name)
        if name in taken:
            alternative = pascal(t.name)  # without making it singular: books becomes Books
            n = 2
            while alternative in taken:
                alternative, n = f"{pascal(t.name)}{n}", n + 1
            if warnings is not None:
                warnings.append(
                    f"tables {taken[name]} and {t.name} would both be concept {name}; {t.name} is {alternative}"
                )
            name = alternative
        taken[name] = t.name
        names[t.name] = name
    return names


def _claim(used: set[str], wanted: str, fallback: str, what: str, warnings: list[str] | None) -> str:
    """A property name not yet used in the concept; a warning when it had to change."""
    name = wanted if wanted not in used else fallback
    n = 2
    while name in used:
        name, n = f"{fallback}_{n}", n + 1
    if name != wanted and warnings is not None:
        warnings.append(f"{what} would be property {wanted}, which is taken; it is {name}")
    used.add(name)
    return name


def tables_to_concepts(tables: list[Table], label: str, warnings: list[str] | None = None) -> list[Concept]:
    """Turn tables into concepts. ``label`` names the source kind (and SQL dialect) in tags.
    Two tables that would get the same concept name (``book`` and ``books``) get
    distinct names, and a warning says so."""
    cnames = _concept_names(tables, warnings)
    by_name = {t.name: t for t in tables}
    concepts: dict[str, Concept] = {}
    join_links: list[tuple[Table, ForeignKey, ForeignKey]] = []

    def links_two(t: Table, pure: set[str]) -> bool:
        """Two single-column links to tables of the file that become concepts."""
        fks = t.foreign_keys
        return (
            len(fks) == 2
            and all(len(fk.columns) == 1 for fk in fks)
            and all(fk.table in by_name and fk.table not in pure for fk in fks)
        )

    def keyed_by(t: Table) -> bool:
        cols = {c for fk in t.foreign_keys for c in fk.columns}
        return set(t.primary_key) == cols or any(set(u) == cols for u in t.unique_sets)

    # Pure join tables (only the two links) become relations, not concepts. One that
    # other tables link to stays a concept, so those links have a target.
    referenced = {fk.table for t in tables for fk in t.foreign_keys if fk.table != t.name}
    pure = {
        t.name
        for t in tables
        if t.kind != "view"
        and t.name not in referenced
        and {c.name for c in t.columns} == {c for fk in t.foreign_keys for c in fk.columns}
        and (keyed_by(t) or not t.primary_key)
    }
    while True:
        still = {name for name in pure if links_two(by_name[name], pure - {name})}
        if still == pure:
            break
        pure = still

    for t in tables:
        link_cols = {c for fk in t.foreign_keys for c in fk.columns}
        rules, notes = _apply_checks(t, label, link_cols)
        for col in t.columns:
            if col.unsigned and col.name not in link_cols:
                r = rules.setdefault(col.name, {})
                r["min"] = _num(max(Decimal(r["min"]), Decimal(0))) if "min" in r else "0"
        positions = {c.name: i for i, c in enumerate(t.columns)}
        fks = sorted(t.foreign_keys, key=lambda fk: min(positions.get(c, 0) for c in fk.columns))
        fk_cols = {c for fk in fks for c in fk.columns}
        if t.name in pure:
            join_links.append((t, fks[0], fks[1]))
            continue
        if t.kind != "view" and links_two(t, pure) and keyed_by(t):
            join_links.append((t, fks[0], fks[1]))  # join table with data: also a concept

        kind = "view" if t.kind == "view" else "table"
        concept = Concept(cnames[t.name], Tag("auto", f"{label} {kind} {t.name}"))
        if t.comment:
            concept.add(Statement("definition", value=t.comment, tag=AUTO))
        if t.kind == "view":
            concept.add(Statement("note", value="database view (read-only)", tag=AUTO))
        for parent in t.inherits:
            concept.add(Statement("is_a", name=cnames.get(parent, concept_name(parent)), tag=AUTO))
        single_key = len(t.primary_key) == 1
        prop_name: dict[str, str] = {}
        used: set[str] = set()
        for c in t.columns:
            if c.name not in fk_cols:
                prop_name[c.name] = _claim(
                    used, safe_name(c.name), safe_name(c.name), f"{t.name}: column {c.name}", warnings
                )
        fk_names: list[str] = []
        for fk in fks:
            first_column = fk.columns[0]
            name = _claim(
                used,
                relation_name(first_column),
                safe_name(first_column),
                f"{t.name}: foreign key {','.join(fk.columns)}",
                warnings,
            )
            fk_names.append(name)
            for column in fk.columns:  # every column of a composite link is that relation
                prop_name.setdefault(column, name)
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
            line = Statement(
                "has", name=prop_name[col.name], target=col.datatype, flags=tuple(flags), value=col.comment, tag=AUTO
            )
            line.children = _children(col, rules.get(col.name, {}), notes)
            concept.add(line)
        for fk, rel in zip(fks, fk_names, strict=False):
            target = cnames.get(fk.table, concept_name(fk.table))
            required = all(
                (fk_col := t.column(n)) is not None and (fk_col.not_null or n in t.primary_key) for n in fk.columns
            )
            # One link to one row: the link is the key, or a unique column (one-to-one).
            one_to_one = (
                set(fk.columns) == set(t.primary_key)
                or (len(fk.columns) == 1 and fk.columns[0] in t.unique)
                or any(set(u) == set(fk.columns) for u in t.unique_sets)
            )
            source_col = t.column(fk.columns[0]) if len(fk.columns) == 1 else None
            comment = source_col.comment if source_col is not None else ""
            detail = f"fk {t.name}.{','.join(fk.columns)}"
            concept.add(
                Statement(
                    "rel",
                    name=rel,
                    target=target,
                    flags=(("required",) if required else ()) + (("unique",) if one_to_one else ()),
                    value=comment,
                    tag=Tag("auto", detail),
                )
            )
        sets = [list(u) for u in t.unique_sets if len(u) > 1]
        if len(t.primary_key) > 1:
            sets.insert(0, list(t.primary_key))
        for cols in sets:
            names_in_set = list(dict.fromkeys(prop_name.get(c, safe_name(c)) for c in cols))
            if len(names_in_set) > 1:  # a set that is just one link is already on the relation (unique)
                concept.add(Statement("unique", name=", ".join(names_in_set), tag=AUTO))
        for note in [*t.notes, *notes]:
            concept.add(Statement("note", value=note, tag=AUTO))
        for trigger in t.triggers:
            concept.add(Statement("note", value=f"trigger {trigger}", tag=AUTO))
        concepts[t.name] = concept

    for t, owner, other in join_links:
        source = concepts.get(owner.table)
        if source is None:
            continue
        source.add_property(
            Statement(
                "rel",
                name=plural(_link_name(other)),
                target=cnames.get(other.table, concept_name(other.table)),
                flags=("many",),
                tag=Tag("auto", f"via {t.name}"),
            ),
            warnings,
            f"join table {t.name}",
            safe_name(t.name),
        )

    return list(concepts.values())

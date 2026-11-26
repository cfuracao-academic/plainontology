"""Extract concepts from a SQLite database or a .sql schema file.

Tables become concepts, columns become ``has`` lines, foreign keys become
relations, and pure join tables (two foreign keys and nothing else) become a
``many`` relation instead of a concept of their own.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..model import AUTO, Concept, Statement, Tag
from ..naming import concept_name, plural, relation_name, safe_name


@dataclass
class _ForeignKey:
    table: str
    columns: list[str]
    position: int


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def datatype(declared: str) -> str:
    d = (declared or "").upper()
    if not d:
        return "any"
    if "BOOL" in d:
        return "boolean"
    if "DATETIME" in d or "TIMESTAMP" in d:
        return "datetime"
    if "DATE" in d:
        return "date"
    if "INT" in d:
        return "integer"
    if "JSON" in d:
        return "json"
    if any(x in d for x in ("CHAR", "CLOB", "TEXT", "UUID")):
        return "text"
    if "BLOB" in d:
        return "binary"
    if any(x in d for x in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return "decimal"
    return "text"


def _connect(path: Path) -> sqlite3.Connection:
    if path.suffix.lower() == ".sql":
        con = sqlite3.connect(":memory:")
        con.executescript(path.read_text(encoding="utf-8"))
        return con
    if not path.exists():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def extract_sqlite(path: Path) -> list[Concept]:
    con = _connect(path)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        ]
        schema = {}
        for t in tables:
            cols = con.execute(f"PRAGMA table_info({_q(t)})").fetchall()
            positions = {c[1]: c[0] for c in cols}
            groups: dict[int, _ForeignKey] = {}
            for row in con.execute(f"PRAGMA foreign_key_list({_q(t)})").fetchall():
                fk_id, _seq, target, col = row[0], row[1], row[2], row[3]
                fk = groups.setdefault(fk_id, _ForeignKey(target, [], positions.get(col, 0)))
                fk.columns.append(col)
                fk.position = min(fk.position, positions.get(col, 0))
            uniques = set()
            for idx in con.execute(f"PRAGMA index_list({_q(t)})").fetchall():
                if idx[2]:
                    icols = [r[2] for r in con.execute(f"PRAGMA index_info({_q(idx[1])})")]
                    if len(icols) == 1:
                        uniques.add(icols[0])
            schema[t] = (cols, sorted(groups.values(), key=lambda g: g.position), uniques)
    finally:
        con.close()

    concepts: dict[str, Concept] = {}
    join_tables = []
    for t in tables:
        cols, fks, uniques = schema[t]
        fk_cols = {c for fk in fks for c in fk.columns}
        names = {c[1] for c in cols}
        if len(fks) == 2 and names == fk_cols and all(len(fk.columns) == 1 for fk in fks):
            join_tables.append((t, fks))
            continue
        concept = Concept(concept_name(t), Tag("auto", f"sqlite table {t}"))
        pk_cols = [c[1] for c in cols if c[5]]
        notnull = {c[1] for c in cols if c[3]}
        for _cid, col, declared, nn, _default, pk in cols:
            if col in fk_cols:
                continue
            flags = []
            if pk and len(pk_cols) == 1:
                flags.append("key")
            elif nn or pk:
                flags.append("required")
            if col in uniques and "key" not in flags:
                flags.append("unique")
            concept.add(Statement("has", name=safe_name(col), target=datatype(declared), flags=tuple(flags), tag=AUTO))
        for fk in fks:
            rel = relation_name(fk.columns[0]) if len(fk.columns) == 1 else concept_name(fk.table)[:1].lower() + concept_name(fk.table)[1:]
            flags = ("required",) if all(c in notnull for c in fk.columns) else ()
            detail = f"fk {t}.{','.join(fk.columns)}"
            concept.add(Statement("rel", name=rel, target=concept_name(fk.table), flags=flags, tag=Tag("auto", detail)))
        concepts[t] = concept

    for t, (owner, other) in join_tables:
        source = concepts.get(owner.table)
        if source is None:
            continue
        name = plural(relation_name(other.columns[0]))
        source.add(Statement("rel", name=name, target=concept_name(other.table), flags=("many",), tag=Tag("auto", f"via {t}")))

    return list(concepts.values())

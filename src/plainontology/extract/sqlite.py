"""Extract concepts from a SQLite database or a SQLite-compatible .sql file.

For PostgreSQL and MySQL schemas use the ``postgres:`` and ``mysql:`` sources,
which read each dialect's own syntax. The rules for turning tables into
concepts are shared; see ``sqlmodel.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..model import Concept
from .sqlmodel import Column, ForeignKey, Table, map_type, tables_to_concepts


def datatype(declared: str) -> str:
    return map_type(declared)


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _connect(path: Path) -> sqlite3.Connection:
    if path.suffix.lower() == ".sql":
        con = sqlite3.connect(":memory:")
        con.executescript(path.read_text(encoding="utf-8"))
        return con
    if not path.exists():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def read_tables(con: sqlite3.Connection) -> list[Table]:
    names = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        )
    ]
    tables = []
    for name in names:
        t = Table(name)
        keyed = []
        for _cid, col, declared, not_null, _default, pk in con.execute(f"PRAGMA table_info({_q(name)})"):
            t.columns.append(Column(col, datatype(declared), bool(not_null)))
            if pk:
                keyed.append((pk, col))
        t.primary_key = [c for _, c in sorted(keyed)]
        groups: dict[int, ForeignKey] = {}
        for row in con.execute(f"PRAGMA foreign_key_list({_q(name)})"):
            groups.setdefault(row[0], ForeignKey([], row[2])).columns.append(row[3])
        t.foreign_keys = list(groups.values())
        for idx in con.execute(f"PRAGMA index_list({_q(name)})").fetchall():
            if idx[2]:
                cols = [r[2] for r in con.execute(f"PRAGMA index_info({_q(idx[1])})")]
                if len(cols) == 1:
                    t.unique.add(cols[0])
        tables.append(t)
    return tables


def extract_sqlite(path: Path) -> list[Concept]:
    con = _connect(path)
    try:
        tables = read_tables(con)
    finally:
        con.close()
    return tables_to_concepts(tables, "sqlite")

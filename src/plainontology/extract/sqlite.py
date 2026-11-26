"""Extract concepts from a SQLite database or a SQLite-compatible .sql file.

For PostgreSQL and MySQL schemas use the ``postgres:`` and ``mysql:`` sources,
which read each dialect's own syntax. The rules for turning tables into
concepts are shared; see ``sqlmodel.py``. CHECK constraints and triggers are
read when sqlglot is installed (any of the ``postgres``/``mysql`` extras).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..model import Concept
from .sqlmodel import Column, ForeignKey, Table, declared_length, map_type, tables_to_concepts


def datatype(declared: str) -> str:
    return map_type(declared)


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


_DENIED = {
    getattr(sqlite3, "SQLITE_ATTACH", 24),
    getattr(sqlite3, "SQLITE_DETACH", 25),
}


def _no_files(action: int, arg1, arg2, dbname, source) -> int:
    """Authorizer for .sql sources: the script runs in memory and may not open,
    attach or write any other database file."""
    if action in _DENIED:
        return sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_FUNCTION", 31) and str(arg2).lower() in (
        "load_extension",
        "readfile",
        "writefile",
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _connect(path: Path) -> sqlite3.Connection:
    if path.suffix.lower() == ".sql":
        con = sqlite3.connect(":memory:")
        con.set_authorizer(_no_files)
        try:
            con.executescript(path.read_text(encoding="utf-8-sig"))
        except sqlite3.DatabaseError as exc:
            con.close()
            raise ValueError(f"{path.name}: {exc}") from None
        return con
    if not path.exists():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _columns(con: sqlite3.Connection, name: str, table: Table) -> list[tuple[int, str]]:
    keyed = []
    for _cid, col, declared, not_null, _default, pk in con.execute(f"PRAGMA table_info({_q(name)})"):
        table.columns.append(Column(col, datatype(declared), bool(not_null), max_length=declared_length(declared)))
        if pk:
            keyed.append((pk, col))
    return keyed


def read_tables(con: sqlite3.Connection) -> list[Table]:
    rows = con.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
    ).fetchall()
    tables = []
    for kind, name, _sql in rows:
        if kind == "trigger":
            continue
        t = Table(name, kind=kind)
        keyed = _columns(con, name, t)
        if kind == "view":
            for col in t.columns:
                col.not_null = False
            tables.append(t)
            continue
        t.primary_key = [c for _, c in sorted(keyed)]
        groups: dict[int, ForeignKey] = {}
        for row in con.execute(f"PRAGMA foreign_key_list({_q(name)})"):
            groups.setdefault(row[0], ForeignKey([], row[2])).columns.append(row[3])
        t.foreign_keys = list(groups.values())
        for idx in con.execute(f"PRAGMA index_list({_q(name)})").fetchall():
            if idx[2] and idx[3] != "pk":
                cols = [r[2] for r in con.execute(f"PRAGMA index_info({_q(idx[1])})")]
                if len(cols) == 1:
                    t.unique.add(cols[0])
                elif cols:
                    t.unique_sets.append(cols)
        tables.append(t)

    # CHECK constraints and triggers live only in the stored SQL text.
    try:
        from .sqldump import _Reader, sqlglot
    except ImportError:  # pragma: no cover
        sqlglot = None  # type: ignore[assignment]
    if sqlglot is not None:
        reader = _Reader("sqlite")
        reader.read(";\n".join(sql for kind, _n, sql in rows if sql and kind in ("table", "trigger")) + ";\n")
        for t in tables:
            parsed = reader.tables.get(t.name)
            if parsed is not None:
                t.checks = parsed.checks
                t.triggers = parsed.triggers
    return tables


def extract_sqlite(path: Path, warnings: list[str] | None = None) -> list[Concept]:
    con = _connect(path)
    try:
        tables = read_tables(con)
    finally:
        con.close()
    return tables_to_concepts(tables, "sqlite", warnings)

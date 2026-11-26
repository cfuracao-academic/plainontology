"""Extract concepts from a SQLite database or a SQLite-compatible .sql file.

For PostgreSQL and MySQL schemas use the ``postgres:`` and ``mysql:`` sources,
which read each dialect's own syntax. The rules for turning tables into
concepts are shared; see ``sqlmodel.py``. CHECK constraints and triggers are
read when sqlglot is installed (any of the ``postgres``/``mysql`` extras).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
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


SCRIPT_SECONDS = 10


def _run_script(con: sqlite3.Connection, script: str, deadline: float) -> None:
    """Run a script statement by statement, checking the time between them: SQLite's
    progress handler counts afresh for each statement, so many quick-looking slow
    statements would never trip it."""
    chunk = ""
    for line in script.splitlines(keepends=True):
        chunk += line
        if sqlite3.complete_statement(chunk):
            if time.monotonic() >= deadline:
                raise sqlite3.OperationalError("interrupted")
            con.executescript(chunk)
            chunk = ""
    if chunk.strip():
        con.executescript(chunk)  # an unfinished statement: SQLite reports the error


def _connect(path: Path) -> sqlite3.Connection:
    if path.suffix.lower() == ".sql":
        con = sqlite3.connect(":memory:")
        con.set_authorizer(_no_files)
        if hasattr(con, "setlimit"):  # Python 3.11+: no string or blob over 10 MB
            con.setlimit(getattr(sqlite3, "SQLITE_LIMIT_LENGTH", 0), 10_000_000)
        deadline = time.monotonic() + SCRIPT_SECONDS
        # A schema file loads in milliseconds; stop one that runs away, whether in one
        # endless statement (progress handler) or in many slow ones (timer, whole script).
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1_000)
        fired = threading.Event()

        def stop() -> None:
            fired.set()
            con.interrupt()

        timer = threading.Timer(SCRIPT_SECONDS, stop)
        timer.daemon = True
        timer.start()
        try:
            _run_script(con, path.read_text(encoding="utf-8-sig"), deadline)
        except sqlite3.DatabaseError as exc:
            con.close()
            late = fired.is_set() or time.monotonic() >= deadline
            reason = f"stopped after {SCRIPT_SECONDS} seconds" if late else str(exc)
            raise ValueError(f"{path.name}: {reason}") from None
        finally:
            timer.cancel()
        con.set_progress_handler(None, 0)
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


def read_tables(con: sqlite3.Connection, warnings: list[str] | None = None) -> list[Table]:
    rows = con.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
    ).fetchall()
    tables = []
    for kind, name, _sql in rows:
        if kind == "trigger":
            continue
        t = Table(name, kind=kind)
        try:
            keyed = _columns(con, name, t)
        except sqlite3.OperationalError as exc:  # a view over a table that no longer exists
            if warnings is not None:
                warnings.append(f"{kind} {name} could not be read and is skipped: {exc}")
            continue
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
                partial = len(idx) > 4 and idx[4]
                if partial or any(c is None for c in cols):
                    # Unique only among some rows, or on an expression: a note, not a flag.
                    row = con.execute(
                        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (idx[1],)
                    ).fetchone()
                    m = re.search(
                        r"\bON\s+\S+\s*\((.*)\)\s*(WHERE\s+.*)?$", " ".join((row[0] or "").split()), re.I | re.S
                    )
                    on, where = (m[1], m[2] or "") if m else (", ".join(c or "?" for c in cols), "")
                    t.notes.append(f"unique ({on})" + (" where " + where[5:].strip() if where else ""))
                    continue
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
        tables = read_tables(con, warnings)
    finally:
        con.close()
    return tables_to_concepts(tables, "sqlite", warnings)

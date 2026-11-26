"""Read the schema of a live PostgreSQL or MySQL database (read-only).

The connection URL is never stored in the ontology file with a password:
write the source as ``postgres:env:DATABASE_URL`` and keep the URL in that
environment variable, or use a URL without a password (for example with
``~/.pgpass`` or a socket). Needs SQLAlchemy and a driver:
``pip install 'plainontology[postgres]'`` or ``'plainontology[mysql]'``.

Reads tables and views, comments, keys, foreign keys, unique and CHECK
constraints, triggers, and, in PostgreSQL, every non-system schema.
"""

from __future__ import annotations

import os
import re

from .sqlmodel import Column, ForeignKey, Table, enum_type, map_type, qualified

_DRIVERS = {
    "postgresql://": "postgresql+psycopg://",
    "postgres://": "postgresql+psycopg://",
    "mysql://": "mysql+pymysql://",
    "mariadb://": "mysql+pymysql://",
}
_PG_SYSTEM = re.compile(r"^(pg_catalog|information_schema|pg_toast.*|pg_temp.*)$")
_SQLITE_TRIGGER = re.compile(r"TRIGGER\s+[`\"]?([\w$]+)[`\"]?\s+(BEFORE|AFTER|INSTEAD\s+OF)\s+(\w+)", re.I)


def is_live(location: str) -> bool:
    return location.startswith("env:") or "://" in location


def with_driver(url: str) -> str:
    """Add the driver PlainOntology installs (psycopg, pymysql) to a plain URL."""
    for prefix, driver in _DRIVERS.items():
        if url.startswith(prefix):
            return driver + url[len(prefix):]
    return url


def resolve_url(location: str) -> str:
    """Turn ``env:VAR`` or a URL into a SQLAlchemy URL with a driver.

    A URL written in the source line itself must not hold a password, because
    the source line is saved in the ontology file.
    """
    if location.startswith("env:"):
        var = location[4:]
        url = os.environ.get(var)
        if not url:
            raise ValueError(f"environment variable {var} is not set (it should hold the database URL)")
        return with_driver(url)
    try:
        from sqlalchemy.engine import make_url
    except ImportError:
        make_url = None
    if make_url is not None and make_url(location).password:
        raise ValueError(
            "the database URL contains a password, which would be saved in the ontology file; "
            "put the URL in an environment variable and use KIND:env:VARIABLE instead"
        )
    return with_driver(location)


def _datatype(sqltype, dialect) -> tuple[str, bool, int | None, str]:
    """(datatype, many, max length, pattern) for a reflected column type."""
    import sqlalchemy as sa

    if isinstance(sqltype, sa.Enum) and sqltype.enums:
        return enum_type(list(sqltype.enums)), False, None, ""
    if isinstance(sqltype, sa.ARRAY):
        return _datatype(sqltype.item_type, dialect)[0], True, None, ""
    if getattr(sqltype, "data_type", None) is not None and type(sqltype).__name__ == "DOMAIN":
        datatype, many, length, pattern = _datatype(sqltype.data_type, dialect)
        if getattr(sqltype, "check", None) is not None:
            from .sqlcheck import analyse

            for f in analyse(str(sqltype.check), "postgres"):
                if f.column.upper() == "VALUE" and f.kind == "pattern":
                    pattern = str(f.value)
                elif f.column.upper() == "VALUE" and f.kind == "enum" and datatype in ("text", "any"):
                    datatype = enum_type(f.value)
        return datatype, many, length, pattern
    if isinstance(sqltype, sa.Boolean):
        return "boolean", False, None, ""
    if isinstance(sqltype, sa.types.NullType):
        return "any", False, None, ""
    length = getattr(sqltype, "length", None) if isinstance(sqltype, sa.String) else None
    try:
        declared = sqltype.compile(dialect=dialect)
    except Exception:  # some reflected types cannot compile on their own
        declared = type(sqltype).__name__
    return map_type(declared), False, (length if isinstance(length, int) else None), ""


def _safe(call, default):
    try:
        return call()
    except NotImplementedError:
        return default


def _triggers(conn, dialect_name: str, schema: str | None) -> dict[str, list[str]]:
    import sqlalchemy as sa

    found: dict[str, list[str]] = {}
    if dialect_name in ("postgresql", "mysql", "mariadb"):
        where = "event_object_schema = :schema" if schema else "event_object_schema = DATABASE()"
        if dialect_name == "postgresql" and not schema:
            where = "event_object_schema = current_schema()"
        rows = conn.execute(sa.text(
            "SELECT trigger_name, action_timing, event_manipulation, event_object_table "
            f"FROM information_schema.triggers WHERE {where} ORDER BY trigger_name"), {"schema": schema} if schema else {})
        grouped: dict[tuple[str, str, str], list[str]] = {}
        for name, timing, event, table in rows:
            grouped.setdefault((table, name, timing), []).append(event)
        for (table, name, timing), events in grouped.items():
            found.setdefault(table, []).append(f"{name}: {timing} {', '.join(dict.fromkeys(events))}")
    elif dialect_name == "sqlite":
        for name, table, sql in conn.execute(sa.text("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'")):
            m = _SQLITE_TRIGGER.search(sql or "")
            if m:
                found.setdefault(table, []).append(f"{name}: {' '.join(m[2].upper().split())} {m[3].upper()}")
    return found


def _read(insp, conn, engine, name: str, schema: str | None, default: str | None, kind: str, triggers) -> Table:
    defaults = (default.lower(),) if default else ()
    t = Table(qualified(schema, name, defaults), kind=kind)
    for col in insp.get_columns(name, schema=schema):
        datatype, many, length, pattern = _datatype(col["type"], engine.dialect)
        if kind == "view":  # views report types and lengths only, as the dump reader does
            t.columns.append(Column(col["name"], datatype, False, many, "", length))
            continue
        t.columns.append(Column(col["name"], datatype, not col.get("nullable", True), many,
                                col.get("comment") or "", length, pattern))
    comment = _safe(lambda: insp.get_table_comment(name, schema=schema), {}) or {}
    t.comment = comment.get("text") or ""
    if kind == "view":
        return t
    t.primary_key = list(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
    for fk in insp.get_foreign_keys(name, schema=schema):
        if fk.get("constrained_columns") and fk.get("referred_table"):
            target = qualified(fk.get("referred_schema") or schema, fk["referred_table"], defaults)
            t.foreign_keys.append(ForeignKey(list(fk["constrained_columns"]), target))
    sets: list[list[str]] = []
    for uc in _safe(lambda: insp.get_unique_constraints(name, schema=schema), []):
        sets.append(list(uc.get("column_names") or []))
    for ix in insp.get_indexes(name, schema=schema):
        if ix.get("unique"):
            sets.append([c for c in (ix.get("column_names") or []) if c])
    for cols in sets:
        if len(cols) == 1:
            t.unique.add(cols[0])
        elif cols and cols not in t.unique_sets and set(cols) != set(t.primary_key):
            t.unique_sets.append(cols)
    for ck in _safe(lambda: insp.get_check_constraints(name, schema=schema), []):
        if ck.get("sqltext"):
            t.checks.append(str(ck["sqltext"]))
    t.triggers = triggers.get(name, [])
    return t


def tables_from_database(url: str) -> list[Table]:
    try:
        import sqlalchemy as sa
    except ImportError:
        raise ValueError("reading a live database needs SQLAlchemy and a driver: pip install 'plainontology[postgres]' "
                         "or 'plainontology[mysql]'") from None
    engine = sa.create_engine(url)
    try:
        insp = sa.inspect(engine)
        default = insp.default_schema_name
        if engine.dialect.name == "postgresql":
            schemas = [s for s in insp.get_schema_names() if not _PG_SYSTEM.match(s)]
            schemas.sort(key=lambda s: (s != default, s))
        else:
            schemas = [None]
        tables: list[Table] = []
        with engine.connect() as conn:
            for schema in schemas:
                triggers = _safe(lambda: _triggers(conn, engine.dialect.name, schema), {})
                for name in insp.get_table_names(schema=schema):
                    tables.append(_read(insp, conn, engine, name, schema, default, "table", triggers))
                for name in _safe(lambda: insp.get_view_names(schema=schema), []):
                    tables.append(_read(insp, conn, engine, name, schema, default, "view", triggers))
        return tables
    finally:
        engine.dispose()

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

from .sqlcheck import for_column
from .sqlmodel import Column, ForeignKey, Table, declared_length, enum_type, fold_partition, map_type, qualified

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
            return driver + url[len(prefix) :]
    return url


def resolve_url(location: str) -> str:
    """Turn ``env:VAR`` or a URL into a SQLAlchemy URL with a driver.

    A URL written in the source line itself must not hold a password, because
    the source line is saved in the ontology file.
    """
    if os.environ.get("PLAINONTO_NO_LIVE"):
        raise ValueError(
            "live database sources are switched off here (PLAINONTO_NO_LIVE is set); "
            "use a schema dump, or set allow-live-databases in the GitHub Action"
        )
    if location.startswith("env:"):
        var = location[4:]
        url = os.environ.get(var)
        if not url:
            raise ValueError(f"environment variable {var} is not set (it should hold the database URL)")
        return with_driver(url)
    if _has_password(location):
        raise ValueError(
            "the database URL contains a password, which would be saved in the ontology file; "
            "put the URL in an environment variable and use KIND:env:VARIABLE instead"
        )
    return with_driver(location)


_SECRET_KEYS = {"password", "passwd", "pwd", "sslpassword", "pass", "secret", "token", "access_token"}


def _has_password(url: str) -> bool:
    """True if a URL carries a password, in the user part or in its query string.
    Works without SQLAlchemy, so the check never silently switches off."""
    from urllib.parse import parse_qsl, urlsplit

    try:
        parts = urlsplit(url)
        if parts.password:
            return True
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return True  # unreadable: refuse rather than risk saving a secret
    return any(key.lower() in _SECRET_KEYS for key, _ in query)


def _datatype(sqltype, dialect, domains: dict[str, str] | None = None) -> tuple[str, bool, int | None, str]:
    """(datatype, many, max length, pattern) for a reflected column type.

    ``domains`` maps PostgreSQL domain names to their declared type text
    (``character varying(10)``): SQLAlchemy's DOMAIN type drops the length.
    """
    import sqlalchemy as sa

    if isinstance(sqltype, sa.Enum) and sqltype.enums:
        return enum_type(list(sqltype.enums)), False, None, ""
    if isinstance(sqltype, sa.ARRAY):
        return _datatype(sqltype.item_type, dialect, domains)[0], True, None, ""
    if getattr(sqltype, "data_type", None) is not None and type(sqltype).__name__ == "DOMAIN":
        datatype, many, length, pattern = _datatype(sqltype.data_type, dialect, domains)
        domain_name = str(getattr(sqltype, "name", "") or "").split(".")[-1].strip('"').lower()
        declared = (domains or {}).get(domain_name)
        if length is None and declared:
            length = declared_length(declared)
        return datatype, many, length, pattern  # the domain's checks are added to the column (see _read)
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
        rows = conn.execute(
            sa.text(
                "SELECT trigger_name, action_timing, event_manipulation, event_object_table "
                f"FROM information_schema.triggers WHERE {where} ORDER BY trigger_name"
            ),
            {"schema": schema} if schema else {},
        )
        grouped: dict[tuple[str, str, str], list[str]] = {}
        for name, timing, event, table in rows:
            grouped.setdefault((table, name, timing), []).append(event)
        for (table, name, timing), events in grouped.items():
            found.setdefault(table, []).append(f"{name}: {timing} {', '.join(dict.fromkeys(events))}")
    elif dialect_name == "sqlite":
        for name, table, sql in conn.execute(
            sa.text("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'")
        ):
            m = _SQLITE_TRIGGER.search(sql or "")
            if m:
                found.setdefault(table, []).append(f"{name}: {' '.join(m[2].upper().split())} {m[3].upper()}")
    return found


def _read(
    insp,
    conn,
    engine,
    name: str,
    schema: str | None,
    default: str | None,
    kind: str,
    triggers,
    domains: dict[str, str],
    domain_checks: dict[str, list[str]],
) -> Table:
    defaults = (default.lower(),) if default else ()
    t = Table(qualified(schema, name, defaults), kind=kind)
    for col in insp.get_columns(name, schema=schema):
        datatype, many, length, pattern = _datatype(col["type"], engine.dialect, domains)
        if kind != "view" and type(col["type"]).__name__ == "DOMAIN":
            domain_name = str(getattr(col["type"], "name", "") or "").split(".")[-1].strip('"').lower()
            t.checks += [for_column(sql, col["name"], "postgres") for sql in domain_checks.get(domain_name, [])]
        if kind == "view":  # views report types and lengths only, as the dump reader does
            t.columns.append(Column(col["name"], datatype, False, many, "", length))
            continue
        t.columns.append(
            Column(
                col["name"], datatype, not col.get("nullable", True), many, col.get("comment") or "", length, pattern
            )
        )
    comment = _safe(lambda: insp.get_table_comment(name, schema=schema), {}) or {}
    t.comment = comment.get("text") or ""
    if kind == "view":
        return t
    t.primary_key = list(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
    for fk in insp.get_foreign_keys(name, schema=schema):
        if fk.get("constrained_columns") and fk.get("referred_table"):
            # SQLAlchemy reports no referred_schema when the target is in the default schema.
            target = qualified(fk.get("referred_schema"), fk["referred_table"], defaults)
            t.foreign_keys.append(ForeignKey(list(fk["constrained_columns"]), target))
    sets: list[list[str]] = []
    for uc in _safe(lambda: insp.get_unique_constraints(name, schema=schema), []):
        sets.append(list(uc.get("column_names") or []))
    for ix in insp.get_indexes(name, schema=schema):
        if not ix.get("unique"):
            continue
        columns = list(ix.get("column_names") or [])
        where = (ix.get("dialect_options") or {}).get("postgresql_where")
        if where or any(c is None for c in columns):
            # Unique only among some rows, or on an expression: a note, not a flag.
            on = ", ".join(str(e) for e in (ix.get("expressions") or columns))
            t.notes.append(f"unique ({on})" + (f" where {where}" if where else ""))
            continue
        sets.append([c for c in columns if c])
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


CONNECT_SECONDS = 10


def tables_from_database(url: str) -> list[Table]:
    try:
        import sqlalchemy as sa
    except ImportError:
        raise ValueError(
            "reading a live database needs SQLAlchemy and a driver: pip install 'plainontology[postgres]' "
            "or 'plainontology[mysql]'"
        ) from None
    # A host that does not answer fails after CONNECT_SECONDS, not after minutes (CI, pre-commit).
    backend = sa.engine.make_url(url).get_backend_name()
    connect_args = {"connect_timeout": CONNECT_SECONDS} if backend in ("postgresql", "mysql", "mariadb") else {}
    engine = sa.create_engine(url, connect_args=connect_args)
    try:
        insp = sa.inspect(engine)
        default = insp.default_schema_name
        domains: dict[str, str] = {}
        domain_checks: dict[str, list[str]] = {}
        partition_of: dict[str, str] = {}  # partition -> parent (PostgreSQL)
        partition_keys: dict[str, str] = {}
        inherits: dict[str, list[str]] = {}  # table -> parents (PostgreSQL INHERITS)
        inherited: dict[str, set[str]] = {}  # table -> columns it only inherits
        if engine.dialect.name == "postgresql":
            schemas: list[str | None] = [s for s in insp.get_schema_names() if not _PG_SYSTEM.match(s)]
            schemas.sort(key=lambda s: (s != default, s))
            with engine.connect() as conn:
                rows = conn.execute(
                    sa.text(
                        "SELECT t.typname, format_type(t.typbasetype, t.typtypmod) FROM pg_catalog.pg_type t "
                        "WHERE t.typtype = 'd'"
                    )
                )
                domains = {str(name).lower(): str(declared) for name, declared in rows}
                for d in _safe(lambda: insp.get_domains(schema="*"), []):  # type: ignore[attr-defined]
                    domain_checks[str(d["name"]).lower()] = [str(c["check"]) for c in d.get("constraints") or []]
                partitions = conn.execute(
                    sa.text(
                        "SELECT cn.nspname, c.relname, pn.nspname, p.relname, pg_get_partkeydef(p.oid) "
                        "FROM pg_catalog.pg_inherits i "
                        "JOIN pg_catalog.pg_class c ON c.oid = i.inhrelid "
                        "JOIN pg_catalog.pg_namespace cn ON cn.oid = c.relnamespace "
                        "JOIN pg_catalog.pg_class p ON p.oid = i.inhparent "
                        "JOIN pg_catalog.pg_namespace pn ON pn.oid = p.relnamespace "
                        "WHERE c.relispartition"
                    )
                )
                defaults = (default.lower(),) if default else ()
                for c_schema, child, p_schema, parent, key in partitions:
                    partition_of[qualified(c_schema, child, defaults)] = qualified(p_schema, parent, defaults)
                    if key:
                        partition_keys[qualified(p_schema, parent, defaults)] = " ".join(str(key).split())
                parents = conn.execute(
                    sa.text(
                        "SELECT cn.nspname, c.relname, pn.nspname, p.relname "
                        "FROM pg_catalog.pg_inherits i "
                        "JOIN pg_catalog.pg_class c ON c.oid = i.inhrelid "
                        "JOIN pg_catalog.pg_namespace cn ON cn.oid = c.relnamespace "
                        "JOIN pg_catalog.pg_class p ON p.oid = i.inhparent "
                        "JOIN pg_catalog.pg_namespace pn ON pn.oid = p.relnamespace "
                        "WHERE NOT c.relispartition ORDER BY i.inhseqno"
                    )
                )
                for c_schema, child, p_schema, parent in parents:
                    child_name = qualified(c_schema, child, defaults)
                    inherits.setdefault(child_name, []).append(qualified(p_schema, parent, defaults))
                columns = conn.execute(
                    sa.text(
                        "SELECT n.nspname, c.relname, a.attname FROM pg_catalog.pg_attribute a "
                        "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
                        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE NOT a.attislocal AND a.attnum > 0 AND NOT a.attisdropped AND NOT c.relispartition"
                    )
                )
                for c_schema, child, column in columns:
                    inherited.setdefault(qualified(c_schema, child, defaults), set()).add(column)
        else:
            schemas = [None]  # MySQL and SQLite: the connected database only
        tables: list[Table] = []
        dialect_name = engine.dialect.name
        with engine.connect() as conn:
            for schema in schemas:
                triggers = _safe(lambda s=schema: _triggers(conn, dialect_name, s), {})
                for name in insp.get_table_names(schema=schema):
                    tables.append(
                        _read(insp, conn, engine, name, schema, default, "table", triggers, domains, domain_checks)
                    )
                for name in _safe(lambda s=schema: insp.get_view_names(schema=s), []):
                    tables.append(
                        _read(insp, conn, engine, name, schema, default, "view", triggers, domains, domain_checks)
                    )
        by_name = {t.name: t for t in tables}
        for name, parents_of in inherits.items():
            if name in by_name:  # the child lists only its own columns, as in a dump
                t = by_name[name]
                gone = inherited.get(name, set())
                t.inherits = parents_of
                t.columns = [c for c in t.columns if c.name not in gone]
                t.checks = [c for c in t.checks if not any(re.search(rf"\b{re.escape(g)}\b", c) for g in gone)]
        counts: dict[str, int] = {}
        roots: dict[str, str] = {}
        for child in partition_of:
            root, seen = partition_of[child], {child}
            while root in partition_of and root not in seen:  # sub-partitions fold into the top table
                seen.add(root)
                root = partition_of[root]
            if root in by_name:
                roots[child] = root
                counts[root] = counts.get(root, 0) + 1
                if child in by_name:
                    fold_partition(by_name[root], by_name[child])
        tables = [t for t in tables if t.name not in roots]
        for parent, n in counts.items():
            key = partition_keys.get(parent)
            by_name[parent].notes.append(
                f"partitioned{' by ' + key if key else ''} into {n} table{'' if n == 1 else 's'}"
            )
        return tables
    finally:
        engine.dispose()

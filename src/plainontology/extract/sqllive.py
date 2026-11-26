"""Read the schema of a live PostgreSQL or MySQL database (read-only).

The connection URL is never stored in the ontology file with a password:
write the source as ``postgres:env:DATABASE_URL`` and keep the URL in that
environment variable, or use a URL without a password (for example with
``~/.pgpass`` or a socket). Needs SQLAlchemy and a driver:
``pip install 'plainontology[postgres]'`` or ``'plainontology[mysql]'``.
"""

from __future__ import annotations

import os

from .sqlmodel import Column, ForeignKey, Table, enum_type, map_type

_DRIVERS = {
    "postgresql://": "postgresql+psycopg://",
    "postgres://": "postgresql+psycopg://",
    "mysql://": "mysql+pymysql://",
    "mariadb://": "mysql+pymysql://",
}


def is_live(location: str) -> bool:
    return location.startswith("env:") or "://" in location


def resolve_url(location: str) -> str:
    """Turn ``env:VAR`` or a URL into a SQLAlchemy URL with a driver."""
    if location.startswith("env:"):
        var = location[4:]
        url = os.environ.get(var)
        if not url:
            raise ValueError(f"environment variable {var} is not set (it should hold the database URL)")
    else:
        url = location
        try:
            from sqlalchemy.engine import make_url
        except ImportError:
            make_url = None
        if make_url is not None and make_url(url).password:
            raise ValueError(
                "the database URL contains a password, which would be saved in the ontology file; "
                "put the URL in an environment variable and use KIND:env:VARIABLE instead"
            )
    for prefix, driver in _DRIVERS.items():
        if url.startswith(prefix):
            return driver + url[len(prefix):]
    return url


def _datatype(sqltype, dialect) -> tuple[str, bool]:
    import sqlalchemy as sa

    if isinstance(sqltype, sa.Enum) and sqltype.enums:
        return enum_type(list(sqltype.enums)), False
    if isinstance(sqltype, sa.ARRAY):
        return _datatype(sqltype.item_type, dialect)[0], True
    if isinstance(sqltype, sa.Boolean):
        return "boolean", False
    try:
        declared = sqltype.compile(dialect=dialect)
    except Exception:  # some reflected types cannot compile on their own
        declared = type(sqltype).__name__
    return map_type(declared), False


def tables_from_database(url: str, schema: str | None = None) -> list[Table]:
    try:
        import sqlalchemy as sa
    except ImportError:
        raise ValueError("reading a live database needs SQLAlchemy and a driver: pip install 'plainontology[postgres]' "
                         "or 'plainontology[mysql]'") from None
    engine = sa.create_engine(url)
    try:
        insp = sa.inspect(engine)
        tables = []
        for name in insp.get_table_names(schema=schema):
            t = Table(name)
            for col in insp.get_columns(name, schema=schema):
                datatype, many = _datatype(col["type"], engine.dialect)
                t.columns.append(Column(col["name"], datatype, not col.get("nullable", True), many))
            t.primary_key = list(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
            for fk in insp.get_foreign_keys(name, schema=schema):
                if fk.get("constrained_columns") and fk.get("referred_table"):
                    t.foreign_keys.append(ForeignKey(list(fk["constrained_columns"]), fk["referred_table"]))
            for uc in insp.get_unique_constraints(name, schema=schema):
                if len(uc.get("column_names") or []) == 1:
                    t.unique.add(uc["column_names"][0])
            for ix in insp.get_indexes(name, schema=schema):
                if ix.get("unique") and len(ix.get("column_names") or []) == 1 and ix["column_names"][0]:
                    t.unique.add(ix["column_names"][0])
            tables.append(t)
        return tables
    finally:
        engine.dispose()

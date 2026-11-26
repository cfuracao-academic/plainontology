"""Read PostgreSQL and MySQL schema files in their own dialect.

Works on ``pg_dump --schema-only`` and ``mysqldump --no-data`` output as well
as hand-written DDL: CREATE TABLE, ALTER TABLE ... ADD CONSTRAINT, CREATE
UNIQUE INDEX, PostgreSQL ``CREATE TYPE ... AS ENUM`` and arrays, MySQL
``enum(...)`` and ``tinyint(1)``. Statements that are not about the schema
(SET, SELECT, LOCK TABLES, functions) are skipped. Needs ``sqlglot``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .sqlmodel import Column, ForeignKey, Table, enum_type, map_type

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ErrorLevel, ParseError
except ImportError:  # pragma: no cover - reported when used
    sqlglot = None

logging.getLogger("sqlglot").setLevel(logging.ERROR)

_COPY_BLOCK = re.compile(r"^COPY [^\n]*FROM stdin;\n.*?^\\\.\n?", re.M | re.S)
_INSERTS = re.compile(r"^INSERT INTO [^\n]*;\n", re.M)

_INTEGER = {"INT", "BIGINT", "SMALLINT", "MEDIUMINT", "TINYINT", "SERIAL", "BIGSERIAL", "SMALLSERIAL", "UINT",
            "UBIGINT", "USMALLINT", "UMEDIUMINT", "UTINYINT", "INT128", "INT256", "UINT128", "UINT256", "YEAR"}
_DECIMAL = {"DECIMAL", "DOUBLE", "FLOAT", "MONEY", "SMALLMONEY", "UDECIMAL", "BIGDECIMAL", "DECIMAL32",
            "DECIMAL64", "DECIMAL128", "DECIMAL256", "UDOUBLE"}
_DATETIME = {"DATETIME", "DATETIME2", "DATETIME64", "TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMPLTZ", "TIMESTAMPNTZ",
             "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "SMALLDATETIME", "DATETIMEOFFSET"}
_BINARY = {"BINARY", "VARBINARY", "BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB", "BYTEA", "IMAGE"}


def _require() -> None:
    if sqlglot is None:
        raise ValueError("reading PostgreSQL or MySQL schema files needs sqlglot: pip install 'plainontology[postgres]'")


def _name(node) -> str:
    """Table name without schema prefix (``public.books`` becomes ``books``)."""
    if isinstance(node, exp.Schema):
        node = node.this
    return node.name if node is not None else ""


def _columns(node) -> list[str]:
    if node is None:
        return []
    items = node.expressions if hasattr(node, "expressions") else []
    return [i.name for i in items if i.name]


class _Reader:
    def __init__(self, dialect: str):
        self.dialect = dialect
        self.enums: dict[str, list[str]] = {}
        self.tables: dict[str, Table] = {}
        self.skipped = 0

    # types -------------------------------------------------------------
    def datatype(self, kind) -> tuple[str, bool]:
        if kind is None:
            return "any", False
        dtype = kind.this.name if hasattr(kind.this, "name") else str(kind.this)
        if dtype == "ARRAY":
            inner = kind.expressions[0] if kind.expressions else None
            return self.datatype(inner)[0], True
        if dtype == "ENUM":
            return enum_type([e.name for e in kind.expressions]), False
        if dtype == "USERDEFINED":
            key = kind.sql(self.dialect).split(".")[-1].strip('"`').lower()
            if key in self.enums:
                return enum_type(self.enums[key]), False
            return "any", False
        if dtype == "TINYINT" and self.dialect == "mysql" and [e.sql() for e in kind.expressions] == ["1"]:
            return "boolean", False
        if dtype in ("BOOLEAN", "BIT"):
            return "boolean", False
        if dtype in _INTEGER:
            return "integer", False
        if dtype in _DECIMAL:
            return "decimal", False
        if dtype == "DATE" or dtype == "DATE32":
            return "date", False
        if dtype in _DATETIME:
            return "datetime", False
        if dtype in ("JSON", "JSONB"):
            return "json", False
        if dtype in _BINARY:
            return "binary", False
        return map_type(kind.sql(self.dialect)), False

    # statements --------------------------------------------------------
    def table(self, name: str) -> Table:
        return self.tables.setdefault(name, Table(name))

    def constraint(self, t: Table, node) -> None:
        if isinstance(node, exp.Constraint):
            for inner in node.expressions:
                self.constraint(t, inner)
        elif isinstance(node, exp.PrimaryKey):
            t.primary_key = _columns(node)
        elif isinstance(node, exp.ForeignKey):
            ref = node.args.get("reference")
            target = ref.this if ref is not None else None
            if target is not None:
                t.foreign_keys.append(ForeignKey(_columns(node), _name(target)))
        elif isinstance(node, exp.UniqueColumnConstraint):
            cols = _columns(node.this)
            if len(cols) == 1:
                t.unique.add(cols[0])

    def create_table(self, schema) -> None:
        t = self.table(_name(schema))
        for item in schema.expressions:
            if not isinstance(item, exp.ColumnDef):
                self.constraint(t, item)
                continue
            datatype, many = self.datatype(item.args.get("kind"))
            col = Column(item.name, datatype, many=many)
            for c in item.args.get("constraints") or []:
                kind = c.args.get("kind")
                if isinstance(kind, exp.NotNullColumnConstraint) and not kind.args.get("allow_null"):
                    col.not_null = True
                elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
                    t.primary_key = [item.name]
                elif isinstance(kind, exp.UniqueColumnConstraint):
                    t.unique.add(item.name)
                elif isinstance(kind, exp.Reference) and kind.this is not None:
                    t.foreign_keys.append(ForeignKey([item.name], _name(kind.this)))
            t.columns.append(col)

    def statement(self, node) -> None:
        if isinstance(node, exp.Create):
            kind = (node.args.get("kind") or "").upper()
            if kind == "TABLE" and isinstance(node.this, exp.Schema):
                self.create_table(node.this)
            elif kind == "TYPE":
                expression = node.args.get("expression")
                if isinstance(expression, exp.DataType) and expression.this.name == "ENUM":
                    self.enums[_name(node.this).lower()] = [e.name for e in expression.expressions]
            elif kind == "INDEX" and node.args.get("unique") and isinstance(node.this, exp.Index):
                index = node.this
                params = index.args.get("params")
                cols = [c.this.name for c in (params.args.get("columns") if params else None) or [] if hasattr(c, "this")]
                table = index.args.get("table")
                if table is not None and len(cols) == 1:
                    self.table(_name(table)).unique.add(cols[0])
        elif isinstance(node, getattr(exp, "Alter", getattr(exp, "AlterTable", ()))):
            t = self.table(_name(node.this))
            for action in node.args.get("actions") or []:
                if isinstance(action, getattr(exp, "AddConstraint", ())):
                    for item in action.expressions:
                        self.constraint(t, item)
                else:
                    self.constraint(t, action)

    def read(self, sql: str) -> None:
        sql = _INSERTS.sub("", _COPY_BLOCK.sub("", sql))
        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except ParseError:
            statements = []
            for chunk in re.split(r";\s*\n", sql):
                if not chunk.strip():
                    continue
                try:
                    statements += sqlglot.parse(chunk, read=self.dialect, error_level=ErrorLevel.RAISE)
                except ParseError:
                    self.skipped += 1
        enum_first = sorted((s for s in statements if s is not None), key=lambda s: not (
            isinstance(s, exp.Create) and (s.args.get("kind") or "").upper() == "TYPE"))
        for node in enum_first:
            self.statement(node)


def tables_from_ddl(sql: str, dialect: str) -> list[Table]:
    """Parse DDL in ``dialect`` ("postgres" or "mysql") into tables, in file order."""
    _require()
    reader = _Reader(dialect)
    reader.read(sql)
    return [t for t in reader.tables.values() if t.columns]


def tables_from_file(path: Path, dialect: str) -> list[Table]:
    return tables_from_ddl(path.read_text(encoding="utf-8"), dialect)

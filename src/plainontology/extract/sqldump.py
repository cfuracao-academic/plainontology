"""Read PostgreSQL, MySQL (and SQLite) schema files in their own dialect.

Works on ``pg_dump --schema-only`` and ``mysqldump --no-data`` output as well
as hand-written DDL:

* CREATE TABLE and ALTER TABLE (ADD, DROP, RENAME, CHANGE and MODIFY COLUMN,
  RENAME TO, ADD CONSTRAINT for keys, foreign keys,
  unique, CHECK), CREATE UNIQUE INDEX
* COMMENT ON TABLE / VIEW / COLUMN (PostgreSQL), column ``COMMENT '...'`` and
  table ``COMMENT='...'`` (MySQL)
* CREATE VIEW, with column types and lengths taken from the tables it selects from
* CREATE TYPE ... AS ENUM, composite types, CREATE DOMAIN, arrays (PostgreSQL);
  ``enum(...)`` and ``tinyint(1)`` (MySQL)
* CREATE TRIGGER, including MySQL's versioned-comment form
* schemas: tables outside ``public`` keep their schema (``archive.orders``)
* partitions fold into their parent table, with a note
* a CREATE TABLE the parser rejects is read column by column; anything
  skipped is reported as a warning, never dropped silently

Everything else in a dump (SET, data, functions) is skipped. Needs ``sqlglot``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .sqlmodel import Column, ForeignKey, Table, declared_length, enum_type, fold_partition, map_type, qualified

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import SqlglotError  # parse and tokenizer errors
except ImportError:  # pragma: no cover - reported when used
    sqlglot = None  # type: ignore[assignment]

logging.getLogger("sqlglot").setLevel(logging.ERROR)

DEFAULT_SCHEMAS = {"postgres": ("public",), "mysql": (), "sqlite": ("main",)}

_QNAME = r'((?:[`"]?[\w$]+[`"]?\.)?[`"]?[\w$]+[`"]?)'
_VIRTUAL = re.compile(r"(GENERATED\s+ALWAYS\s+AS\s*\(.*?\))\s+VIRTUAL\b", re.I | re.S)
_ATTACH = re.compile(rf"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?{_QNAME}\s+ATTACH\s+PARTITION\s+{_QNAME}", re.I)
_PARTITION_OF = re.compile(rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{_QNAME}\s+PARTITION\s+OF\s+{_QNAME}", re.I)
_PARTITION_BY = re.compile(
    rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{_QNAME}\s*\((?:(?!CREATE\s+TABLE).)*?\)\s*"
    r"PARTITION\s+BY\s+(\w+)\s*\(([^)]*)\)",
    re.I | re.S,
)
_SCHEMA_STATEMENT = re.compile(
    r"^\s*(CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNLOGGED\s+|TEMP\w*\s+)?(?:TABLE|VIEW|TYPE|DOMAIN|UNIQUE\s+INDEX)|ALTER\s+TABLE|COMMENT\s+ON)\b",
    re.I,
)
_CREATE_TABLE = re.compile(
    r"^\s*(CREATE\s+(?:UNLOGGED\s+|TEMP\w*\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\S+)\s*\((.*)\)([^)]*)$", re.I | re.S
)
_COPY_BLOCK = re.compile(r"^COPY [^\n]*FROM stdin;\n.*?^\\\.\n?", re.M | re.S)
_INSERTS = re.compile(r"^INSERT INTO [^\n]*;\n", re.M)
_VERSIONED = re.compile(r"/\*!\d*\s*(.*?)\*/", re.S)
_DELIMITER = re.compile(r"^DELIMITER\s+\S+\s*$", re.M | re.I)
_IDENT = r'[`"]?([\w$]+)[`"]?'
_DOMAIN = re.compile(
    rf"CREATE\s+DOMAIN\s+(?:{_IDENT}\.)?{_IDENT}\s+(?:AS\s+)?([A-Za-z][\w ]*?(?:\([^)]*\))?)\s*"
    r"(?:DEFAULT|NOT|NULL|CHECK|CONSTRAINT|COLLATE|;)(.*?);",
    re.I | re.S,
)
_DOMAIN_IN = re.compile(r"VALUE\s+IN\s*\(([^)]*)\)", re.I)
_DOMAIN_REGEX = re.compile(r"VALUE\)?(?:::\w+)?\s*~\s*'((?:[^']|'')*)'", re.I)
_TRIGGER = re.compile(
    rf"CREATE\s+(?:OR\s+REPLACE\s+)?(?:DEFINER\s*=\s*\S+\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+{_IDENT}\s+"
    rf"(BEFORE|AFTER|INSTEAD\s+OF)\s+(.+?)\s+ON\s+(?:ONLY\s+)?(?:{_IDENT}\.)?{_IDENT}",
    re.I | re.S,
)

_INTEGER = {
    "INT",
    "BIGINT",
    "SMALLINT",
    "MEDIUMINT",
    "TINYINT",
    "SERIAL",
    "BIGSERIAL",
    "SMALLSERIAL",
    "UINT",
    "UBIGINT",
    "USMALLINT",
    "UMEDIUMINT",
    "UTINYINT",
    "INT128",
    "INT256",
    "UINT128",
    "UINT256",
    "YEAR",
}
_DECIMAL = {
    "DECIMAL",
    "DOUBLE",
    "FLOAT",
    "MONEY",
    "SMALLMONEY",
    "UDECIMAL",
    "BIGDECIMAL",
    "DECIMAL32",
    "DECIMAL64",
    "DECIMAL128",
    "DECIMAL256",
    "UDOUBLE",
}
_DATETIME = {
    "DATETIME",
    "DATETIME2",
    "DATETIME64",
    "TIMESTAMP",
    "TIMESTAMPTZ",
    "TIMESTAMPLTZ",
    "TIMESTAMPNTZ",
    "TIMESTAMP_S",
    "TIMESTAMP_MS",
    "TIMESTAMP_NS",
    "SMALLDATETIME",
    "DATETIMEOFFSET",
}
_BINARY = {"BINARY", "VARBINARY", "BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB", "BYTEA", "IMAGE"}
_SIZED_TEXT = {"VARCHAR", "CHAR", "NVARCHAR", "NCHAR"}


def _is(node: Any, name: str) -> bool:
    """isinstance against a sqlglot class that older or newer versions may not have."""
    cls = getattr(exp, name, None)
    return cls is not None and isinstance(node, cls)


def _require() -> None:
    if sqlglot is None:
        raise ValueError(
            "reading PostgreSQL or MySQL schema files needs sqlglot: pip install 'plainontology[postgres]'"
        )


def _columns(node) -> list[str]:
    if node is None:
        return []
    items = node.expressions if hasattr(node, "expressions") else []
    return [i.name for i in items if i.name]


def _strip_quotes(text: str) -> list[str]:
    return [v.strip().strip("'\"") for v in text.split(",") if v.strip()]


class _Reader:
    def __init__(self, dialect: str):
        self.dialect = dialect
        self.defaults = DEFAULT_SCHEMAS.get(dialect, ())
        self.enums: dict[str, list[str]] = {}
        self.domains: dict[str, tuple[str, list[str] | None, str]] = {}
        self.composites: set[str] = set()
        self.tables: dict[str, Table] = {}
        self.views: list[tuple[str, Any]] = []
        self.table_comments: dict[str, str] = {}
        self.constraint_names: dict[tuple[str, str], tuple[str, object]] = {}
        self.warnings: list[str] = []
        self.partition_of: dict[str, str] = {}  # partition -> parent table
        self.partition_keys: dict[str, str] = {}  # parent -> "RANGE (day)"
        self.column_comments: dict[tuple[str, str], str] = {}

    # names -------------------------------------------------------------
    def qual(self, node) -> str:
        if isinstance(node, exp.Schema):
            node = node.this
        if node is None:
            return ""
        schema = node.args.get("db")
        schema_name = schema.name if schema is not None and self.dialect != "mysql" else None
        return qualified(schema_name, node.name, self.defaults)

    def table(self, name: str) -> Table:
        return self.tables.setdefault(name, Table(name))

    # types -------------------------------------------------------------
    def datatype(self, kind) -> tuple[str, bool, int | None]:
        if kind is None:
            return "any", False, None
        dtype = kind.this.name if hasattr(kind.this, "name") else str(kind.this)
        if dtype == "ARRAY":
            inner = kind.expressions[0] if kind.expressions else None
            return self.datatype(inner)[0], True, None
        if dtype == "ENUM":
            return enum_type([e.name for e in kind.expressions]), False, None
        if dtype == "USERDEFINED":
            key = kind.sql(self.dialect).split(".")[-1].strip('"`').lower()
            if key in self.enums:
                return enum_type(self.enums[key]), False, None
            if key in self.domains:
                base, values, _pattern = self.domains[key]
                return (enum_type(values) if values else map_type(base)), False, declared_length(base)
            if key in self.composites:
                return "json", False, None
            return "any", False, None
        params = [e.sql() for e in kind.expressions]
        if dtype == "TINYINT" and self.dialect == "mysql" and params == ["1"]:
            return "boolean", False, None
        if dtype in ("BOOLEAN", "BIT"):
            return "boolean", False, None
        if dtype in _INTEGER:
            return "integer", False, None
        if dtype in _DECIMAL:
            return "decimal", False, None
        if dtype in ("DATE", "DATE32"):
            return "date", False, None
        if dtype in _DATETIME:
            return "datetime", False, None
        if dtype in ("JSON", "JSONB"):
            return "json", False, None
        if dtype in _BINARY:
            return "binary", False, None
        length = int(params[0]) if dtype in _SIZED_TEXT and params and params[0].isdigit() else None
        return map_type(kind.sql(self.dialect)), False, length

    # statements --------------------------------------------------------
    def remember(self, t: Table, name: str | None, kind: str, value) -> None:
        """Record a constraint under its name (and PostgreSQL's default name) for DROP CONSTRAINT."""
        if name:
            self.constraint_names[(t.name, name.lower())] = (kind, value)

    def constraint(self, t: Table, node, name: str | None = None) -> None:
        bare = t.name.split(".")[-1]
        if isinstance(node, exp.Constraint):
            for inner in node.expressions:
                self.constraint(t, inner, node.this.name if node.this is not None else None)
        elif isinstance(node, exp.PrimaryKey):
            t.primary_key = _columns(node)
            self.remember(t, name or f"{bare}_pkey", "pk", None)
        elif isinstance(node, exp.ForeignKey):
            ref = node.args.get("reference")
            target = ref.this if ref is not None else None
            if target is not None:
                fk = ForeignKey(_columns(node), self.qual(target))
                t.foreign_keys.append(fk)
                self.remember(t, name or f"{bare}_{'_'.join(fk.columns)}_fkey", "fk", fk)
        elif isinstance(node, exp.UniqueColumnConstraint):
            schema = node.this
            cols = _columns(schema)
            key_name = name or (
                schema.this.name if isinstance(schema, exp.Schema) and schema.this is not None else None
            )
            if len(cols) == 1:
                t.unique.add(cols[0])
                self.remember(t, key_name or f"{bare}_{cols[0]}_key", "unique", cols[0])
            elif cols:
                t.unique_sets.append(cols)
                self.remember(t, key_name or f"{bare}_{'_'.join(cols)}_key", "unique_set", cols)
        elif isinstance(node, exp.CheckColumnConstraint) and node.this is not None:
            sql = node.this.sql(self.dialect)
            t.checks.append(sql)
            self.remember(t, name, "check", sql)

    def drop_constraint(self, t: Table, name: str) -> None:
        found = self.constraint_names.pop((t.name, name.lower()), None)
        if found is None:
            if name.lower() in {c.lower() for c in t.unique}:  # MySQL DROP INDEX on a unique column
                t.unique = {c for c in t.unique if c.lower() != name.lower()}
                return
            self.warnings.append(f"{t.name}: could not find constraint {name} to drop; the result may still list it")
            return
        kind, value = found
        if kind == "pk":
            t.primary_key = []
        elif kind == "fk":
            t.foreign_keys = [fk for fk in t.foreign_keys if fk is not value]
        elif kind == "unique":
            t.unique.discard(value)
        elif kind == "unique_set":
            t.unique_sets = [u for u in t.unique_sets if u != value]
        elif kind == "check" and value in t.checks:
            t.checks.remove(value)

    def column(self, t: Table, item) -> Column:
        kind_node = item.args.get("kind")
        datatype, many, length = self.datatype(kind_node)
        col = Column(item.name, datatype, many=many, max_length=length)
        if kind_node is not None and getattr(kind_node.this, "name", "") == "USERDEFINED":
            domain = self.domains.get(kind_node.sql(self.dialect).split(".")[-1].strip('"`').lower())
            if domain:
                col.pattern = domain[2]
        for c in item.args.get("constraints") or []:
            kind = c.args.get("kind")
            if isinstance(kind, exp.NotNullColumnConstraint) and not kind.args.get("allow_null"):
                col.not_null = True
            elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
                t.primary_key = [item.name]
                self.remember(t, f"{t.name.split('.')[-1]}_pkey", "pk", None)
            elif isinstance(kind, exp.UniqueColumnConstraint):
                t.unique.add(item.name)
                self.remember(t, f"{t.name.split('.')[-1]}_{item.name}_key", "unique", item.name)
            elif isinstance(kind, exp.Reference) and kind.this is not None:
                fk = ForeignKey([item.name], self.qual(kind.this))
                t.foreign_keys.append(fk)
                self.remember(t, f"{t.name.split('.')[-1]}_{item.name}_fkey", "fk", fk)
            elif isinstance(kind, exp.CommentColumnConstraint) and kind.this is not None:
                col.comment = kind.this.name
            elif isinstance(kind, exp.CheckColumnConstraint) and kind.this is not None:
                t.checks.append(kind.this.sql(self.dialect))
        return col

    def put_column(self, t: Table, col: Column, replace: str | None = None) -> None:
        """Add a column, or replace the column named ``replace`` (default: the same name)."""
        old = replace or col.name
        for i, existing in enumerate(t.columns):
            if existing.name == old:
                t.columns[i] = col
                if old != col.name:
                    self.rename_column(t, old, col.name, keep_column=True)
                return
        t.columns.append(col)

    def rename_column(self, t: Table, old: str, new: str, keep_column: bool = False) -> None:
        if not keep_column:
            for col in t.columns:
                if col.name == old:
                    col.name = new
        swap = lambda cols: [new if c == old else c for c in cols]  # noqa: E731
        t.primary_key = swap(t.primary_key)
        for fk in t.foreign_keys:
            fk.columns = swap(fk.columns)
        t.unique = {new if c == old else c for c in t.unique}
        t.unique_sets = [swap(u) for u in t.unique_sets]

    def drop_column(self, t: Table, name: str) -> None:
        t.columns = [c for c in t.columns if c.name != name]
        t.primary_key = [c for c in t.primary_key if c != name]
        t.foreign_keys = [fk for fk in t.foreign_keys if name not in fk.columns]
        t.unique.discard(name)
        t.unique_sets = [u for u in t.unique_sets if name not in u]

    def rename_table(self, old: str, new: str) -> None:
        if old not in self.tables:
            return
        t = self.tables.pop(old)
        t.name = new
        self.tables[new] = t
        for (table, cname), _value in list(self.constraint_names.items()):
            if table == old:
                self.constraint_names[(new, cname)] = self.constraint_names.pop((table, cname))
        for other in self.tables.values():
            for fk in other.foreign_keys:
                if fk.table == old:
                    fk.table = new

    def alter(self, node) -> None:
        name = self.qual(node.this)
        t = self.table(name)
        for action in node.args.get("actions") or []:
            if _is(action, "AddConstraint"):
                for item in action.expressions:
                    self.constraint(t, item)
            elif isinstance(action, exp.ColumnDef):  # ADD COLUMN
                self.put_column(t, self.column(t, action))
            elif _is(action, "ModifyColumn") and isinstance(action.this, exp.ColumnDef):
                old = action.args.get("rename_from")  # MySQL CHANGE old new ... / MODIFY
                self.put_column(t, self.column(t, action.this), old.name if old is not None else None)
            elif isinstance(action, exp.Drop) and (action.args.get("kind") or "").upper() == "COLUMN":
                for target in action.args.get("tables") or []:
                    self.drop_column(t, target.name)
            elif isinstance(action, exp.Drop) and (action.args.get("kind") or "").upper() in (
                "CONSTRAINT",
                "FOREIGN KEY",
                "INDEX",
                "KEY",
                "PRIMARY KEY",
            ):
                if (action.args.get("kind") or "").upper() == "PRIMARY KEY":
                    t.primary_key = []
                for target in action.args.get("tables") or []:
                    self.drop_constraint(t, target.name)
            elif _is(action, "AlterColumn"):
                col = t.column(action.this.name) if action.this is not None else None
                if col is None:
                    self.warnings.append(
                        f"{t.name}: ALTER COLUMN {action.this.name if action.this else '?'}: no such column"
                    )
                    continue
                if action.args.get("dtype") is not None:
                    col.datatype, col.many, col.max_length = self.datatype(action.args["dtype"])
                if action.args.get("allow_null") is False:
                    col.not_null = True
                elif action.args.get("drop") and action.args.get("allow_null"):
                    col.not_null = False
            elif _is(action, "RenameColumn"):
                self.rename_column(t, action.this.name, action.args["to"].name)
            elif _is(action, "AlterRename") and action.this is not None:
                schema = node.this.args.get("db")
                new = qualified(
                    schema.name if schema is not None and self.dialect != "mysql" else None,
                    action.this.name,
                    self.defaults,
                )
                self.rename_table(name, new)
                t = self.tables[new]
            else:
                self.constraint(t, action)

    def create_table(self, node) -> None:
        schema = node.this
        name = self.qual(schema)
        if name in self.tables and self.tables[name].columns:
            if node.args.get("exists"):  # CREATE TABLE IF NOT EXISTS keeps the existing table
                return
            self.tables[name] = Table(name)
        t = self.table(name)
        for item in schema.expressions:
            if not isinstance(item, exp.ColumnDef):
                self.constraint(t, item)
                continue
            t.columns.append(self.column(t, item))
        properties = node.args.get("properties")
        for prop in properties.expressions if properties else []:
            if isinstance(prop, exp.SchemaCommentProperty) and prop.this is not None:
                t.comment = prop.this.name

    def statement(self, node) -> None:
        if isinstance(node, exp.Create):
            kind = (node.args.get("kind") or "").upper()
            if kind == "TABLE" and isinstance(node.this, exp.Schema):
                self.create_table(node)
            elif kind == "TYPE":
                expression = node.args.get("expression")
                name = self.qual(node.this).split(".")[-1].lower()
                if isinstance(expression, exp.DataType) and expression.this.name == "ENUM":
                    self.enums[name] = [e.name for e in expression.expressions]
                elif isinstance(expression, exp.Schema):
                    self.composites.add(name)
            elif kind == "VIEW" and node.args.get("expression") is not None:
                self.views.append((self.qual(node.this), node.args["expression"]))
            elif kind == "INDEX" and node.args.get("unique") and isinstance(node.this, exp.Index):
                index = node.this
                params = index.args.get("params")
                cols = [
                    c.this.name for c in (params.args.get("columns") if params else None) or [] if hasattr(c, "this")
                ]
                table = index.args.get("table")
                if table is not None and cols:
                    t = self.table(self.qual(table))
                    if len(cols) == 1:
                        t.unique.add(cols[0])
                    else:
                        t.unique_sets.append(cols)
        elif _is(node, "Alter") or _is(node, "AlterTable"):
            self.alter(node)
        elif isinstance(node, exp.Comment) and node.args.get("expression") is not None:
            kind = (node.args.get("kind") or "").upper()
            text = node.args["expression"].name
            target = node.this
            if kind in ("TABLE", "VIEW", "MATERIALIZED VIEW"):
                name = self.qual(target)
                if name in self.tables and self.tables[name].columns:
                    self.tables[name].comment = text
                else:
                    self.table_comments[name] = text  # a view, resolved later
            elif kind == "COLUMN" and isinstance(target, exp.Column):
                schema = target.args.get("db")
                table = qualified(schema.name if schema is not None else None, target.table, self.defaults)
                col = self.tables[table].column(target.name) if table in self.tables else None
                if col is not None:
                    col.comment = text
                else:
                    self.column_comments[(table, target.name)] = text
        elif isinstance(node, exp.Drop) and (node.args.get("kind") or "").upper() in ("TABLE", "VIEW"):
            for target in node.args.get("tables") or []:
                name = self.qual(target)
                self.tables.pop(name, None)
                self.views = [(n, q) for n, q in self.views if n != name]
                self.table_comments.pop(name, None)

    # views -------------------------------------------------------------
    def resolve_view(self, name: str, query) -> Table:
        view = Table(name, kind="view")
        select = query
        while _is(select, "SetOperation") or isinstance(select, exp.Union):
            select = select.this
        if not isinstance(select, exp.Select):
            return view
        aliases: dict[str, str] = {}
        for tbl in select.find_all(exp.Table):
            aliases[tbl.alias_or_name] = self.qual(tbl)
        sources = list(dict.fromkeys(aliases.values()))

        def source_column(table_ref: str, column: str) -> Column | None:
            table_name = aliases.get(table_ref) if table_ref else (sources[0] if len(sources) == 1 else None)
            src = self.tables.get(table_name) if table_name else None
            return src.column(column) if src else None

        for proj in select.expressions:
            if isinstance(proj, exp.Star) or (isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)):
                refs = [aliases.get(proj.table)] if isinstance(proj, exp.Column) and proj.table else sources
                for ref in refs:
                    for col in self.tables[ref].columns if ref in self.tables else []:
                        view.columns.append(Column(col.name, col.datatype, many=col.many, max_length=col.max_length))
                continue
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            while isinstance(inner, (exp.Paren, exp.Cast)):
                inner = inner.this
            src = source_column(inner.table, inner.name) if isinstance(inner, exp.Column) else None
            name_ = proj.alias_or_name or "column"
            if src is not None:
                view.columns.append(Column(name_, src.datatype, many=src.many, max_length=src.max_length))
            else:
                view.columns.append(Column(name_, "any"))
        return view

    # file --------------------------------------------------------------
    def prescan(self, sql: str) -> None:
        for m in _DOMAIN.finditer(sql):
            values = _DOMAIN_IN.search(m[4] or "")
            regex = _DOMAIN_REGEX.search(m[4] or "")
            self.domains[m[2].lower()] = (
                m[3].strip(),
                _strip_quotes(values[1]) if values else None,
                regex[1].replace("''", "'") if regex else "",
            )

    def triggers(self, sql: str) -> None:
        seen = set()
        for m in _TRIGGER.finditer(sql):
            name, timing, events, schema, table = m[1], m[2], m[3], m[4], m[5]
            table_name = qualified(schema if self.dialect != "mysql" else None, table, self.defaults)
            if (table_name, name) in seen or table_name not in self.tables:
                continue
            seen.add((table_name, name))
            event_list = [e.strip().split()[0].upper() for e in re.split(r"\s+OR\s+", events, flags=re.I) if e.strip()]
            self.tables[table_name].triggers.append(
                f"{name}: {' '.join(timing.upper().split())} {', '.join(event_list)}"
            )

    def apply_comments(self) -> None:
        for name, text in self.table_comments.items():
            if name in self.tables:
                self.tables[name].comment = text
        for (name, column), text in self.column_comments.items():
            col = self.tables[name].column(column) if name in self.tables else None
            if col is not None:
                col.comment = text

    def qual_text(self, name: str) -> str:
        parts = [p.strip('`"') for p in name.split(".")]
        schema = parts[-2] if len(parts) > 1 and self.dialect != "mysql" else None
        return qualified(schema, parts[-1], self.defaults)

    def partitions(self, sql: str) -> None:
        for m in _ATTACH.finditer(sql):
            self.partition_of[self.qual_text(m[2])] = self.qual_text(m[1])
        for m in _PARTITION_OF.finditer(sql):
            self.partition_of[self.qual_text(m[1])] = self.qual_text(m[2])
        for m in _PARTITION_BY.finditer(sql):
            self.partition_keys[self.qual_text(m[1])] = f"{m[2].upper()} ({' '.join(m[3].split())})"

    def fold_partitions(self) -> None:
        counts: dict[str, int] = {}
        for child in list(self.partition_of):
            root, seen = self.partition_of[child], {child}
            while root in self.partition_of and root not in seen:
                seen.add(root)
                root = self.partition_of[root]
            if root in self.tables:
                if child in self.tables:
                    fold_partition(self.tables[root], self.tables.pop(child))
                counts[root] = counts.get(root, 0) + 1
        for parent, n in counts.items():
            key = self.partition_keys.get(parent)
            self.tables[parent].notes.append(
                f"partitioned{' by ' + key if key else ''} into {n} table{'' if n == 1 else 's'}"
            )

    def salvage_table(self, chunk: str) -> list:
        """Parse a CREATE TABLE the parser rejects one column at a time, keeping what it can read."""
        m = _CREATE_TABLE.match(chunk)
        if not m:
            return []
        head, body, tail = m[1], m[2], m[3]
        parts, depth, quote, start = [], 0, "", 0
        for i, ch in enumerate(body):
            if quote:
                quote = "" if ch == quote else quote
            elif ch in "'\"`":
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(body[start:i])
                start = i + 1
        parts.append(body[start:])
        good = []
        for part in parts:
            try:
                sqlglot.parse_one(f"{head} ({part})", read=self.dialect)
                good.append(part)
            except SqlglotError:
                self.warnings.append(
                    f"{head.split()[-1]}: skipped a column it could not read: {' '.join(part.split())[:70]}"
                )
        for candidate in (f"{head} ({', '.join(good)}){tail}", f"{head} ({', '.join(good)})"):
            try:
                return [s for s in sqlglot.parse(candidate, read=self.dialect) if s is not None]
            except SqlglotError:
                continue
        return []

    def read(self, sql: str) -> None:
        sql = _INSERTS.sub("", _COPY_BLOCK.sub("", sql))
        sql = _VIRTUAL.sub(r"\1 STORED", sql)  # PostgreSQL 18 virtual generated columns
        if self.dialect == "mysql":
            # mysqldump hides views and triggers in /*!NNNNN ... */ comments.
            sql = _VERSIONED.sub(lambda m: " " + m[1] + " ", sql)
            sql = _DELIMITER.sub("", sql).replace(";;", ";")
        self.prescan(sql)
        self.partitions(sql)
        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except SqlglotError:
            statements = []
            for chunk in re.split(r";\s*\n", sql):
                if not chunk.strip():
                    continue
                try:
                    statements += sqlglot.parse(chunk, read=self.dialect)
                except SqlglotError:
                    body = "\n".join(line for line in chunk.splitlines() if not line.lstrip().startswith("--"))
                    if _CREATE_TABLE.match(body):
                        statements += self.salvage_table(body)
                    elif _SCHEMA_STATEMENT.match(body):
                        self.warnings.append(f"skipped a statement it could not read: {' '.join(body.split())[:70]}")
        types_first = sorted(
            (s for s in statements if s is not None),
            key=lambda s: not (isinstance(s, exp.Create) and (s.args.get("kind") or "").upper() == "TYPE"),
        )
        for node in types_first:
            self.statement(node)
        for name, query in self.views:
            self.tables[name] = self.resolve_view(name, query)
        self.apply_comments()
        self.triggers(sql)
        self.fold_partitions()


def tables_from_ddl(sql: str, dialect: str, warnings: list[str] | None = None) -> list[Table]:
    """Parse DDL in ``dialect`` ("postgres", "mysql" or "sqlite") into tables, in file order.
    Statements and columns that could not be read are described in ``warnings``."""
    _require()
    reader = _Reader(dialect)
    reader.read(sql)
    if warnings is not None:
        warnings.extend(reader.warnings)
    return [t for t in reader.tables.values() if t.columns or t.kind == "view"]


def tables_from_file(path: Path, dialect: str, warnings: list[str] | None = None) -> list[Table]:
    return tables_from_ddl(path.read_text(encoding="utf-8-sig"), dialect, warnings)

"""Turn SQL CHECK constraints into enums and rules where possible.

``status IN ('a', 'b')`` (and PostgreSQL's normalised ``= ANY (ARRAY[...])``)
becomes an enum; ``x >= 0``, ``x BETWEEN 1 AND 5`` and ``char_length(x) <= 20``
become min, max and length rules; ``x ~ 'regex'`` becomes a pattern. Anything else is kept as a note, so no
constraint is silently lost. Needs sqlglot; without it every check is a note.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    import sqlglot
    from sqlglot import exp
except ImportError:  # pragma: no cover
    sqlglot = None


@dataclass
class Finding:
    kind: str  # enum, min, max, length_max, length_min, pattern, note
    column: str = ""
    value: object = None
    exclusive: bool = False


_LENGTH_FUNCS = {"LENGTH", "CHAR_LENGTH", "CHARACTER_LENGTH", "LEN"}


def _strip(node):
    while isinstance(node, (exp.Paren, exp.Cast)):
        node = node.this
    return node


def _column(node) -> str:
    node = _strip(node)
    return node.name if isinstance(node, exp.Column) else ""


def _length_column(node) -> str:
    node = _strip(node)
    if isinstance(node, (exp.Length,)):
        return _column(node.this)
    if isinstance(node, exp.Anonymous) and node.name.upper() in _LENGTH_FUNCS and node.expressions:
        return _column(node.expressions[0])
    return ""


def _number(node):
    node = _strip(node)
    if isinstance(node, exp.Neg):
        inner = _number(node.this)
        return -inner if inner is not None else None
    if isinstance(node, exp.Literal) and not node.is_string:
        text = node.this
        return int(text) if text.lstrip("-").isdigit() else float(text)
    return None


def _strings(nodes) -> list[str] | None:
    out = []
    for n in nodes:
        n = _strip(n)
        if not (isinstance(n, exp.Literal) and n.is_string):
            return None
        out.append(n.this)
    return out


def _conjuncts(node) -> list:
    node = _strip(node) if isinstance(node, exp.Paren) else node
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


def _flip(cls):
    return {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE}[cls]


def _comparison(node) -> list[Finding] | None:
    for cls in (exp.GT, exp.GTE, exp.LT, exp.LTE):
        if isinstance(node, cls):
            left, right = node.this, node.expression
            if _number(left) is not None:
                left, right, cls = right, left, _flip(cls)
            value = _number(right)
            if value is None:
                return None
            lower = cls in (exp.GT, exp.GTE)
            exclusive = cls in (exp.GT, exp.LT)
            if col := _column(left):
                return [Finding("min" if lower else "max", col, value, exclusive)]
            if col := _length_column(left):
                return [Finding("length_min" if lower else "length_max", col, value, exclusive)]
            return None
    return None


def _part(node) -> list[Finding] | None:
    node = _strip(node) if isinstance(node, exp.Paren) else node
    if isinstance(node, exp.In) and (col := _column(node.this)):
        values = _strings(node.expressions)
        return [Finding("enum", col, values)] if values else None
    if isinstance(node, exp.EQ) and isinstance(_strip(node.expression), exp.Any) and (col := _column(node.this)):
        array = _strip(_strip(node.expression).this)
        if isinstance(array, exp.Array):
            values = _strings(array.expressions)
            return [Finding("enum", col, values)] if values else None
        return None
    regexp = getattr(exp, "RegexpLike", None)
    if regexp is not None and isinstance(node, regexp) and (col := _column(node.this)):
        pattern = _strip(node.expression)
        if isinstance(pattern, exp.Literal) and pattern.is_string:
            return [Finding("pattern", col, pattern.this)]
        return None
    if isinstance(node, exp.Between) and (col := _column(node.this)):
        low, high = _number(node.args.get("low")), _number(node.args.get("high"))
        if low is not None and high is not None:
            return [Finding("min", col, low), Finding("max", col, high)]
        return None
    return _comparison(node)


def _normalise(node):
    """Drop brackets around single conditions, as databases do when they store a CHECK."""
    connectives = (exp.And, exp.Or, exp.Not)

    def strip(n):
        if isinstance(n, exp.Paren) and not isinstance(n.this, connectives):
            return n.this
        return n

    return node.transform(strip)


def analyse(sql: str, dialect: str) -> list[Finding]:
    """Findings for one CHECK expression (without the CHECK keyword)."""
    if sqlglot is None:
        return [Finding("note", value=sql)]
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return [Finding("note", value=sql)]
    findings: list[Finding] = []
    for part in _conjuncts(tree):
        found = _part(part)
        if found is None:
            findings.append(Finding("note", value=_normalise(part).sql(dialect)))
        else:
            findings.extend(found)
    return findings

"""Turn SQL CHECK constraints into enums and rules where possible.

``status IN ('a', 'b')`` (and PostgreSQL's normalised ``= ANY (ARRAY[...])``)
becomes an enum; ``x >= 0``, ``x BETWEEN 1 AND 5`` and ``char_length(x) <= 20``
become min, max and length rules; ``x ~ 'regex'`` becomes a pattern. Anything else is kept as a note, so no
constraint is silently lost. Needs sqlglot; without it every check is a note.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import sqlglot
    from sqlglot import exp
except ImportError:  # pragma: no cover
    sqlglot = None  # type: ignore[assignment]


@dataclass
class Finding:
    kind: str  # enum, min, max, length_max, length_min, pattern, note
    column: str = ""
    value: Any = None
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


def _without_null_branch(node):
    """``rule OR col IS NULL`` is the rule for every value (NULL is no value): drop the
    branch when the rule is about that column only."""
    node = _strip(node) if isinstance(node, exp.Paren) else node
    if not isinstance(node, exp.Or):
        return node
    for rule, other in ((node.this, node.expression), (node.expression, node.this)):
        other = _strip(other) if isinstance(other, exp.Paren) else other
        is_null = isinstance(other, exp.Is) and isinstance(other.expression, exp.Null)
        if is_null and (col := _column(other.this)) and {c.name for c in rule.find_all(exp.Column)} == {col}:
            return rule
    return node


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
    iregexp = getattr(exp, "RegexpILike", None)
    if any(c is not None and isinstance(node, c) for c in (regexp, iregexp)) and (col := _column(node.this)):
        pattern = _strip(node.expression)
        if isinstance(pattern, exp.Literal) and pattern.is_string:
            flag = "(?i)" if iregexp is not None and isinstance(node, iregexp) else ""  # ~* ignores case
            return [Finding("pattern", col, flag + pattern.this)]
        return None
    if isinstance(node, exp.EQ):  # char_length(x) = 8, or 8 = char_length(x)
        for side, value in ((node.this, node.expression), (node.expression, node.this)):
            if (col := _length_column(side)) and (n := _number(value)) is not None:
                return [Finding("length_min", col, n), Finding("length_max", col, n)]
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
    for part in _conjuncts(_without_null_branch(tree)):
        found = _part(part)
        if found is None:
            findings.append(Finding("note", value=_normalise(part).sql(dialect)))
        else:
            findings.extend(found)
    return findings


def for_column(sql: str, column: str, dialect: str) -> str:
    """A domain CHECK (written with VALUE) as a CHECK on ``column``."""
    import re

    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return re.sub(r"\bVALUE\b", column, sql, flags=re.I)
    for c in list(tree.find_all(exp.Column)):
        if c.name.upper() == "VALUE" and not c.table:
            c.set("this", exp.to_identifier(column))
    return tree.sql(dialect)


def domain_checks(text: str) -> list[str]:
    """The CHECK (...) expressions in the text after CREATE DOMAIN name AS type."""
    import re

    found = []
    for m in re.finditer(r"\bCHECK\s*\(", text, re.I):
        depth, i, quoted = 1, m.end(), False
        while i < len(text) and depth:
            ch = text[i]
            if ch == "'":
                quoted = not quoted
            elif not quoted:
                depth += ch == "("
                depth -= ch == ")"
            i += 1
        if depth == 0:
            found.append(text[m.end() : i - 1].strip())
    return found

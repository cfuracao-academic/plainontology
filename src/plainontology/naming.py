"""Turn table, column and tool names into concept and property names."""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def singular(word: str) -> str:
    lw = word.lower()
    if len(word) > 4 and lw.endswith("ies"):
        return word[:-3] + ("Y" if word[-1].isupper() else "y")
    if lw.endswith(("sses", "shes", "ches", "xes", "uses")):
        return word[:-2]
    if len(word) > 3 and lw.endswith("s") and not lw.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def plural(word: str) -> str:
    if word.endswith("s"):
        return word
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def pascal(text: str, singularise: bool = False) -> str:
    parts = words(text)
    if not parts:
        return "Thing"
    if singularise:
        parts[-1] = singular(parts[-1])
    name = "".join(p[:1].upper() + p[1:] for p in parts)
    return name if not name[0].isdigit() else "C" + name


def concept_name(table: str) -> str:
    """``book_tags`` becomes ``BookTag``, ``authors`` becomes ``Author``."""
    return pascal(table, singularise=True)


def safe_name(text: str) -> str:
    name = re.sub(r"[^\w.-]", "_", text.strip()) or "_"
    return name if not name[0].isdigit() else "_" + name


def relation_name(column: str) -> str:
    """``author_id`` becomes ``author``."""
    stripped = re.sub(r"(_id|Id|_fk|_key)$", "", column)
    return safe_name(stripped or column)

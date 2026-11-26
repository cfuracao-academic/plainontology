"""Turn table, column and tool names into concept and property names."""

from __future__ import annotations

import re
import unicodedata

_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|[^\W\d_]+")


def _word_char(ch: str) -> bool:
    return ch.isalnum() or unicodedata.category(ch)[0] == "M"  # letters, digits and combining marks


def words(text: str) -> list[str]:
    """Split a name into words. ASCII words split at camelCase; words in other
    scripts are kept whole, combining marks included (café, पुस्तकें)."""
    out: list[str] = []
    run = ""
    for ch in text + " ":
        if _word_char(ch):
            run += ch
            continue
        if run:
            out += _WORD_RE.findall(run) if run.isascii() else [run]
        run = ""
    return out


_IRREGULAR = {
    "people": "person",
    "children": "child",
    "men": "man",
    "women": "woman",
    "mice": "mouse",
    "geese": "goose",
    "feet": "foot",
    "teeth": "tooth",
    "indices": "index",
    "criteria": "criterion",
    "analyses": "analysis",
    "statuses": "status",
    "series": "series",
    "species": "species",
}


# Already singular although they end in s.
_SINGULAR_S = {"news", "canvas", "atlas", "alias", "bias", "gas", "lens", "chaos", "bus", "campus", "census", "virus"}
# Plurals that only add s to a word ending in ie, che, use or ase.
_ADD_S = {
    "movies",
    "cookies",
    "zombies",
    "calories",
    "rookies",
    "goalies",
    "selfies",
    "smoothies",
    "brownies",
    "freebies",
    "pixies",
    "genies",
    "ties",
    "lies",
    "pies",
    "caches",
    "niches",
    "headaches",
    "avalanches",
    "moustaches",
    "mustaches",
    "quiches",
    "cliches",
    "uses",
    "abuses",
    "excuses",
    "fuses",
    "ruses",
    "muses",
}
# Plurals that add es to a word ending in s (alias, aliases).
_ADD_ES = {"aliases", "canvases", "atlases", "biases", "gases", "lenses", "buses", "bonuses", "viruses", "campuses"}


def singular(word: str) -> str:
    lw = word.lower()
    if lw in _IRREGULAR:
        single = _IRREGULAR[lw]
        return single.capitalize() if word[:1].isupper() else single
    if lw in _SINGULAR_S:
        return word
    if lw in _ADD_S:
        return word[:-1]
    if lw in _ADD_ES or lw.endswith("zzes"):
        return word[:-2] if not lw.endswith("zzes") else word[:-3]
    if len(word) > 4 and lw.endswith("ies"):
        return word[:-3] + ("Y" if word[-1].isupper() else "y")
    if lw.endswith("uses") and len(lw) > 5 and lw[-5] in "aeiou":  # houses, warehouses, causes
        return word[:-1]
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
    """A property name the file format accepts: starts with a letter (any script)
    or ``_``, then letters, digits, ``_ . -``."""
    name = "".join(ch if _word_char(ch) or ch in "_.-" else "_" for ch in text.strip()) or "_"
    return name if re.match(r"[^\W\d]", name) else "_" + name


def relation_name(column: str) -> str:
    """``author_id`` becomes ``author``."""
    stripped = re.sub(r"(_id|Id|_fk|_key)$", "", column)
    return safe_name(stripped or column)

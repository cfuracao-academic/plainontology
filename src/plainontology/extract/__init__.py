"""Source extractors. A source is written ``KIND:PATH``, for example
``sqlite:data/library.db`` or ``tools:agent-tools.json``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..model import Concept, Ontology
from .jsonschema import extract_jsonschema
from .sqlite import extract_sqlite
from .tools import extract_tools

EXTRACTORS: dict[str, Callable[[Path], list[Concept]]] = {
    "sqlite": extract_sqlite,
    "jsonschema": extract_jsonschema,
    "tools": extract_tools,
}


def parse_source(spec: str) -> tuple[str, str]:
    kind, sep, path = spec.partition(":")
    if not sep or kind not in EXTRACTORS or not path:
        raise ValueError(f"bad source '{spec}'; expected one of {', '.join(k + ':PATH' for k in EXTRACTORS)}")
    return kind, path


def extract(spec: str, base_dir: Path) -> list[Concept]:
    kind, path = parse_source(spec)
    target = Path(path)
    if not target.is_absolute():
        target = base_dir / target
    return EXTRACTORS[kind](target)


def extract_all(sources: list[str], base_dir: Path) -> Ontology:
    """Run every source and fold the results into one ontology of [auto] lines."""
    onto = Ontology()
    for spec in sources:
        for concept in extract(spec, base_dir):
            onto.add_concept(concept)
    return onto


__all__ = ["EXTRACTORS", "extract", "extract_all", "parse_source"]

"""Source extractors. A source is written ``KIND:LOCATION``.

==========  ===========================================================
Kind        Location
==========  ===========================================================
sqlite      a SQLite database, or a SQLite-compatible .sql file
postgres    a PostgreSQL schema file (pg_dump --schema-only), or a live
            database: ``env:VARIABLE`` holding the URL, or a URL without
            a password
mysql       the same for MySQL and MariaDB (mysqldump --no-data)
jsonschema  a JSON Schema file
openapi     an OpenAPI 3.x or Swagger 2.0 file (JSON or YAML)
dbt         a dbt manifest (target/manifest.json), or the dbt project folder
tools       a JSON file of LLM tool definitions
==========  ===========================================================

File locations are relative to the ontology file.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ..model import Concept, Ontology
from .dbt import extract_dbt
from .jsonschema import extract_jsonschema
from .openapi import extract_openapi
from .sqlite import extract_sqlite
from .sqllive import is_live
from .sqlmodel import tables_to_concepts
from .tools import extract_tools

SQL_KINDS = ("postgres", "mysql")


Extractor = Callable[[str, Path, list[str]], list[Concept]]


def within_root(path: Path) -> Path:
    """With PLAINONTO_SOURCE_ROOT set (the GitHub Action sets it to the checkout),
    a source file must be inside that folder."""
    root = os.environ.get("PLAINONTO_SOURCE_ROOT")
    if root:
        resolved, top = path.resolve(), Path(root).resolve()
        if resolved != top and top not in resolved.parents:
            raise ValueError(f"source file {path} is outside {top} (PLAINONTO_SOURCE_ROOT)")
    return path


def _file(fn: Callable[..., list[Concept]], with_warnings: bool = False) -> Extractor:
    def run(location: str, base_dir: Path, warnings: list[str]) -> list[Concept]:
        path = Path(location)
        path = within_root(path if path.is_absolute() else base_dir / path)
        return fn(path, warnings) if with_warnings else fn(path)

    return run


def _sql(dialect: str) -> Extractor:
    def run(location: str, base_dir: Path, warnings: list[str]) -> list[Concept]:
        if is_live(location):
            from .sqllive import resolve_url, tables_from_database

            tables = tables_from_database(resolve_url(location))
        else:
            from .sqldump import tables_from_file

            path = Path(location)
            tables = tables_from_file(within_root(path if path.is_absolute() else base_dir / path), dialect, warnings)
        return tables_to_concepts(tables, dialect, warnings)

    return run


EXTRACTORS: dict[str, Extractor] = {
    "sqlite": _file(extract_sqlite, with_warnings=True),
    "postgres": _sql("postgres"),
    "mysql": _sql("mysql"),
    "jsonschema": _file(extract_jsonschema, with_warnings=True),
    "openapi": _file(extract_openapi, with_warnings=True),
    "dbt": _file(extract_dbt, with_warnings=True),
    "tools": _file(extract_tools, with_warnings=True),
}


def parse_source(spec: str) -> tuple[str, str]:
    kind, sep, location = spec.partition(":")
    if not sep or kind not in EXTRACTORS or not location:
        raise ValueError(f"bad source '{spec}'; expected one of {', '.join(k + ':LOCATION' for k in EXTRACTORS)}")
    return kind, location


def is_file_location(kind: str, location: str) -> bool:
    return not (kind in SQL_KINDS and is_live(location))


def extract(spec: str, base_dir: Path, warnings: list[str] | None = None) -> list[Concept]:
    kind, location = parse_source(spec)
    return EXTRACTORS[kind](location, base_dir, warnings if warnings is not None else [])


def _shared(name: str, first: str, second: str) -> bool:
    """Concepts every file of a kind defines on purpose (the ``Tool`` and ``Operation`` parents)."""
    for parent, kind in (("Tool", "tools:"), ("Operation", "openapi:")):
        if name == parent and first.startswith(kind) and second.startswith(kind):
            return True
    return False


def extract_all(sources: list[str], base_dir: Path) -> Ontology:
    """Run every source and fold the results into one ontology of [auto] lines.

    When two sources define a concept with the same name, their lines are
    combined (the first source wins where both define the same line) and a
    warning is recorded in ``Ontology.warnings``.
    """
    onto = Ontology()
    origin: dict[str, str] = {}
    for spec in sources:
        problems: list[str] = []
        found = extract(spec, base_dir, problems)
        for message in dict.fromkeys(problems):  # one warning per message, however often it came up
            onto.warnings.append(("", f"{spec}: {message}"))
        seen: set[str] = set()
        for concept in found:
            if concept.name in seen:
                onto.warnings.append(
                    (concept.name, f"produced twice by {spec}; the two are combined (the first wins where they differ)")
                )
            seen.add(concept.name)
            first = origin.setdefault(concept.name, spec)
            if first != spec and not _shared(concept.name, first, spec):
                existing = {s.key(): s for s in onto.concepts[concept.name].statements}
                clashes = sum(
                    1 for s in concept.statements if s.key() in existing and existing[s.key()].body() != s.body()
                )
                detail = f"; {clashes} line(s) differ and the first source wins" if clashes else ""
                onto.warnings.append(
                    (concept.name, f"defined by both {first} and {spec}; their lines are combined{detail}")
                )
            onto.add_concept(concept)
        if not found:
            onto.warnings.append(("", f"{spec}: produced no concepts; is it the right file?"))
    return onto


__all__ = ["EXTRACTORS", "SQL_KINDS", "extract", "extract_all", "is_file_location", "parse_source"]

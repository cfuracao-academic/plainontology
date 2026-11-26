"""High-level operations shared by the CLI and the MCP server."""

from __future__ import annotations

import os
from pathlib import Path

from .extract import extract_all, is_file_location, parse_source
from .extract.sqllive import resolve_url
from .merge import DriftReport, merge
from .model import Ontology
from .textformat import load, save


def relative_source(spec: str, file_path: Path) -> str:
    """Rewrite a source given on the command line (relative to the current
    directory) so it is relative to the ontology file instead. Database URLs
    are kept as they are, after checking they hold no password."""
    kind, path = parse_source(spec)
    if not is_file_location(kind, path):
        if not path.startswith("env:"):
            resolve_url(path)  # raises if the URL would store a password
        return spec
    p = Path(path)
    if p.is_absolute():
        return spec
    rel = os.path.relpath(p.resolve(), file_path.resolve().parent)
    return f"{kind}:{Path(rel).as_posix()}"


def init(file_path: Path, sources: list[str]) -> tuple[Ontology, DriftReport]:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    onto = Ontology(sources=[relative_source(s, file_path) for s in sources])
    fresh = extract_all(onto.sources, file_path.resolve().parent)
    merged, report = merge(onto, fresh)
    save(file_path, merged)
    return merged, report


def compare(file_path: Path) -> tuple[Ontology, Ontology, DriftReport]:
    """Return (current, merged, report) without writing anything."""
    current = load(file_path)
    if not current.sources:
        raise ValueError(f"{file_path} has no 'source' lines, so there is nothing to refresh from")
    fresh = extract_all(current.sources, file_path.resolve().parent)
    merged, report = merge(current, fresh)
    return current, merged, report


def refresh(file_path: Path) -> DriftReport:
    _, merged, report = compare(file_path)
    if report.changes:
        save(file_path, merged)
    return report

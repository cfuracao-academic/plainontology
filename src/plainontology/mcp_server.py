"""Serve a PlainOntology file to AI agents over the Model Context Protocol.

Tools: list_concepts, describe_concept, search, validate_record, drift, and,
only with --allow-proposals, propose. The file is re-read on every call, so a
person's edits are seen at once. Agents never overwrite lines: proposals are
written as ``[proposed]`` lines for a person to accept (remove the tag) or delete.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .api import compare
from .check import validate_record as _validate
from .model import Concept, Ontology, Tag
from .textformat import dump_concept, load, parse_statement, save

INSTRUCTIONS = (
    "This server exposes the ontology (concepts, properties, relations) and semantic layer "
    "(labels, definitions, synonyms, mappings) of a system as a plain text file. "
    "Use list_concepts and search to find the right concept, describe_concept before writing "
    "structured data, and validate_record to check a record you produced against the ontology."
)


def _incoming(onto: Ontology, name: str) -> list[str]:
    return [
        f"{c.name}.{s.name} -> {name}"
        for c in onto.concepts.values()
        for s in c.all("rel")
        if s.target == name
    ]


def describe(onto: Ontology, concept: Concept) -> str:
    lines = dump_concept(concept)
    inherited = [s for s in onto.all_properties(concept) if s not in concept.properties()]
    if inherited:
        lines += ["", "Inherited:"] + [f"  {s.body()}" for s in inherited]
    if incoming := _incoming(onto, concept.name):
        lines += ["", "Used by:"] + [f"  {x}" for x in incoming]
    return "\n".join(lines)


def search_text(onto: Ontology, query: str, limit: int = 10) -> list[str]:
    terms = [t for t in re.split(r"\W+", query.lower()) if t]
    if not terms:
        return []
    scored = []
    for c in onto.concepts.values():
        fields = [(c.name, 3)]
        fields += [(s.value, 2) for s in c.all("label", "synonym")]
        fields += [(s.value, 1) for s in c.all("definition", "note")]
        fields += [(f"{s.name} {s.value}", 1) for s in c.properties()]
        score, hits = 0, []
        for text, weight in fields:
            low = text.lower()
            matched = [t for t in terms if t in low]
            if matched:
                score += weight * len(matched)
                hits.append(text)
        if score:
            definition = c.definition()
            scored.append((score, f"{c.name}" + (f": {definition}" if definition else "") + f"  (matched: {hits[0]})"))
    scored.sort(key=lambda x: -x[0])
    return [text for _, text in scored[:limit]]


def build_server(path: Path, allow_proposals: bool = False) -> MCPServer:
    path = Path(path)
    server = MCPServer(name="plainontology", instructions=INSTRUCTIONS)

    @server.tool()
    def list_concepts() -> str:
        """List every concept in the ontology with its one-line definition."""
        onto = load(path)
        if not onto.concepts:
            return "The ontology has no concepts yet."
        return "\n".join(
            c.name + (f": {c.definition()}" if c.definition() else "") for c in onto.concepts.values()
        )

    @server.tool()
    def describe_concept(name: str) -> str:
        """Show a concept's lines (properties, relations, labels, definition), the
        properties it inherits, and which concepts point to it."""
        onto = load(path)
        concept = onto.find(name)
        if concept is None:
            near = search_text(onto, name, 3)
            return f"No concept named '{name}'." + (" Closest: " + "; ".join(near) if near else "")
        return describe(onto, concept)

    @server.tool()
    def search(query: str) -> str:
        """Find concepts by name, label, synonym, definition or property."""
        hits = search_text(load(path), query)
        return "\n".join(hits) if hits else f"Nothing matches '{query}'."

    @server.tool()
    def validate_record(concept: str, record: dict[str, Any]) -> dict[str, Any]:
        """Check a JSON object against a concept: unknown fields, missing required
        fields, wrong types and enum values. Use it on structured output before acting on it."""
        issues = _validate(load(path), concept, record)
        return {"ok": not issues, "issues": issues}

    @server.tool()
    def drift() -> str:
        """Compare the file with its sources now and report what changed, without writing."""
        _, _, report = compare(path)
        return report.render()

    if allow_proposals:

        @server.tool()
        def propose(concept: str, line: str, reason: str = "agent") -> str:
            """Propose one line for a concept, for example 'synonym "Loan"' or
            'has isbn : text unique'. It is written with a [proposed] tag for a
            person to review; nothing existing is changed."""
            try:
                statement = parse_statement(line)
            except ValueError as exc:
                return f"Not added: {exc}"
            if statement.kind == "comment":
                return "Not added: comments cannot be proposed."
            if not re.fullmatch(r"[A-Za-z_][\w.:/-]*", concept):
                return f"Not added: '{concept}' is not a valid concept name (letters, digits, _ . : / -)."
            detail = re.sub(r"[\[\]\n]", " ", reason).strip()[:80]
            statement.tag = Tag("proposed", detail)
            onto = load(path)
            target = onto.find(concept)
            if target is None:
                target = Concept(concept, Tag("proposed", detail))
                onto.concepts[concept] = target
            existing = next((s for s in target.statements if s.key() == statement.key()), None)
            if existing is not None and existing.body() == statement.body():
                return f"Already present: {existing.render()}"
            target.statements.append(statement)
            save(path, onto)
            return f"Proposed for {target.name}: {statement.render()}"

    @server.resource("ontology://file", name="ontology", mime_type="text/plain",
                     description="The whole ontology file as plain text.")
    def ontology_file() -> str:
        return path.read_text(encoding="utf-8")

    return server

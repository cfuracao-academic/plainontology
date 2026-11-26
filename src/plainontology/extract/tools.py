"""Extract concepts from LLM tool definitions.

Accepts a JSON file holding a list of tools (or an object with a ``tools``
list) in any of these shapes:

* Anthropic Messages API: ``{"name", "description", "input_schema"}``
* OpenAI Chat Completions: ``{"type": "function", "function": {"name", "description", "parameters"}}``
* OpenAI Responses API: ``{"type": "function", "name", "description", "parameters"}``
* MCP ``tools/list`` result: ``{"name", "description", "inputSchema"}``

Every tool becomes a concept that ``is_a Tool``; its parameters become the
concept's properties. This makes the ontology an agent actually acts on
visible next to the one stored in the database.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from ..model import AUTO, Concept, Statement, Tag
from ..naming import pascal
from .jsonschema import SchemaWalker


def iter_tools(doc) -> Iterator[tuple[str, str, dict]]:
    if isinstance(doc, dict):
        doc = doc.get("tools", [doc])
    for tool in doc:
        if not isinstance(tool, dict):
            raise ValueError("each tool definition must be a JSON object")
        if isinstance(tool.get("function"), dict):
            tool = tool["function"]
        schema = tool.get("input_schema") or tool.get("inputSchema") or tool.get("parameters")
        if "name" not in tool or schema is None:
            raise ValueError(f"unrecognised tool definition: {tool.get('name', '?')}")
        yield tool["name"], tool.get("description", ""), schema


def extract_tools(path: Path) -> list[Concept]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    concepts: dict[str, Concept] = {
        "Tool": Concept(
            "Tool",
            Tag("auto", f"tools {path.name}"),
            [
                Statement(
                    "definition", value="An action an AI agent can call; its properties are the call's input.", tag=AUTO
                )
            ],
        )
    }
    for name, description, params in iter_tools(doc):
        walker = SchemaWalker(params, path.name)
        schema = {**params, "description": description or params.get("description", "")}
        schema.pop("title", None)
        concept = walker.object_concept(pascal(name), schema, f"tools {path.name} {name}")
        concept.statements.insert(0, Statement("is_a", name="Tool", tag=AUTO))
        for cname, c in walker.concepts.items():
            concepts.setdefault(cname, c)
    return list(concepts.values())

"""Extract concepts from an OpenAPI description (3.0, 3.1) or Swagger 2.0.

* Every schema in ``components.schemas`` (``definitions`` in Swagger 2.0)
  becomes a concept, read with the JSON Schema rules (references, unions,
  constraints, descriptions).
* Every operation becomes a concept that ``is_a Operation``: its parameters
  (path, query, header) and request body are its properties, and ``returns``
  points to the success response's schema. The name comes from
  ``operationId`` (``listBooks`` becomes ``ListBooks``), or from the method and
  path when there is none.

JSON files need nothing extra; YAML files need PyYAML
(``pip install 'plainontology[openapi]'``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..model import AUTO, Concept, Statement, Tag
from ..naming import pascal, safe_name
from .jsonschema import SchemaWalker, _clean

METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


def load_document(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise ValueError("reading YAML OpenAPI files needs PyYAML: pip install 'plainontology[openapi]'") from None
        doc = yaml.safe_load(text)
    else:
        doc = json.loads(text)
    if not isinstance(doc, dict) or not ("openapi" in doc or "swagger" in doc):
        raise ValueError(f"{path.name} is not an OpenAPI or Swagger document (no 'openapi' or 'swagger' key)")
    return doc


def _operation_name(op: dict, method: str, route: str) -> str:
    if op.get("operationId"):
        return pascal(str(op["operationId"]))
    words = [w for w in re.split(r"[/{}_.-]+", route) if w]
    return pascal(" ".join([method, *words]) or method)


def _json_schema(content: dict | None) -> dict | None:
    """The schema of the first JSON-like media type in a content map."""
    for media, body in (content or {}).items():
        if "json" in media or media == "*/*":
            return body.get("schema") if isinstance(body, dict) else None
    return None


def extract_openapi(path: Path, warnings: list[str] | None = None) -> list[Concept]:
    path = path.resolve()
    doc = load_document(path)
    walker = SchemaWalker(doc, path.name, path)
    if warnings is not None:
        walker.shared.warnings = warnings
    walker.kind = "openapi"
    swagger = "swagger" in doc
    pointer = "#/definitions/" if swagger else "#/components/schemas/"
    schemas = doc.get("definitions", {}) if swagger else (doc.get("components") or {}).get("schemas", {})
    for name in schemas or {}:
        walker.ensure_ref(pointer + name)

    operations: list[tuple[str, str, dict, list[dict]]] = []
    for route, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters") or []
        for method in METHODS:
            if isinstance(item.get(method), dict):
                operations.append((route, method, item[method], shared))
    if operations:
        walker.concepts.setdefault(
            "Operation",
            Concept(
                "Operation",
                Tag("auto", f"openapi {path.name}"),
                [
                    Statement(
                        "definition",
                        value="An HTTP operation of the API; its properties are the request's parameters and body.",
                        tag=AUTO,
                    )
                ],
            ),
        )
    for route, method, op, shared in operations:
        _operation(walker, route, method, op, shared, swagger)
    return list(walker.concepts.values())


def _operation(walker: SchemaWalker, route: str, method: str, op: dict, shared: list, swagger: bool) -> None:
    name = _operation_name(op, method, route)
    if name in walker.concepts:  # a schema of the same name, or GET /books and GET /books/
        wanted, name, n = name, f"{name}Operation", 2
        while name in walker.concepts:
            name, n = f"{wanted}Operation{n}", n + 1
        walker.shared.warnings.append(
            f"{method.upper()} {route} would be concept {wanted}, which is taken; it is {name}"
        )
    concept = Concept(name, Tag("auto", f"openapi {method.upper()} {route}"))
    walker.concepts[name] = concept
    concept.add(Statement("is_a", name="Operation", tag=AUTO))
    description = _clean(op.get("summary") or op.get("description"))
    if description:
        concept.add(Statement("definition", value=description, tag=AUTO))
    concept.add(Statement("note", value=f"{method.upper()} {route}", tag=AUTO))

    params: dict[tuple[str, str], dict] = {}
    for p in [*shared, *(op.get("parameters") or [])]:
        if isinstance(p, dict) and "$ref" in p:
            _, resolved = walker.resolve(p["$ref"])
            p = resolved or {}
        if isinstance(p, dict) and p.get("name") and p.get("in") != "body":
            params[(p.get("in", ""), p["name"])] = p
        elif isinstance(p, dict) and p.get("in") == "body" and swagger:
            _body(walker, concept, p.get("schema"), bool(p.get("required")))
    for (_where, pname), p in params.items():
        schema: dict[str, Any] = p.get("schema") or {
            k: v for k, v in p.items() if k in ("type", "format", "enum", "items")
        }
        if p.get("description") and "description" not in schema:
            schema = {**schema, "description": p["description"]}
        concept.add_property(
            walker._property(name, pname, schema, bool(p.get("required")) or p.get("in") == "path"),
            walker.shared.warnings,
            f"{method.upper()} {route}: {_where} parameter {pname}",
            safe_name(f"{_where}_{pname}"),
        )

    if not swagger:
        body = op.get("requestBody") or {}
        if "$ref" in body:
            _, resolved = walker.resolve(body["$ref"])
            body = resolved or {}
        _body(walker, concept, _json_schema(body.get("content")), bool(body.get("required")))

    responses = op.get("responses") or {}
    for code in sorted(str(c) for c in responses):
        if not code.startswith("2"):
            continue
        response = responses.get(code) or responses.get(int(code)) or {}
        if not isinstance(response, dict):
            continue
        if "$ref" in response:
            _, resolved = walker.resolve(response["$ref"])
            response = resolved or {}
        returned = response.get("schema") if swagger else _json_schema(response.get("content"))
        if returned:
            line = walker._property(name, "returns", returned, False)
            line.value = line.value or _clean(response.get("description"))
            concept.add_property(line, walker.shared.warnings, f"{method.upper()} {route}: response", "response")
        break


def _body(walker: SchemaWalker, concept: Concept, schema: dict | None, required: bool) -> None:
    if not schema:
        return
    composed = any(k in schema for k in ("oneOf", "anyOf", "allOf"))
    if "$ref" in schema or schema.get("type") == "array" or composed or not schema.get("properties"):
        concept.add_property(
            walker._property(concept.name, "body", schema, required),
            walker.shared.warnings,
            f"{concept.name}: request body",
            "request_body",
        )
        return
    for pname, ps in (schema.get("properties") or {}).items():
        concept.add_property(
            walker._property(
                concept.name,
                safe_name(pname),
                ps if isinstance(ps, dict) else {},
                pname in set(schema.get("required") or []),
            ),
            walker.shared.warnings,
            f"{concept.name}: body property {pname}",
            safe_name(f"body_{pname}"),
        )

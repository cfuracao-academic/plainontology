# Changelog

## 0.1.1 (2026-11-26)

- README: demo GIF drawn from real command output, and a "Try it" line (now also shown on PyPI)
- README: badges and Zenodo DOI; DOI added to CITATION.cff
- No code changes

## 0.1.0 (2026-11-26)

First release.

- Plain text format, version 0.1 (see SPEC.md)
- Extractors: `sqlite` (database or `.sql` file), `jsonschema`, `tools` (Anthropic, OpenAI and MCP tool definitions)
- Commands: `init`, `refresh`, `drift`, `check`, `export`, `validate`, `mcp`
- Exports: Turtle (OWL + SKOS), LinkML
- MCP server: `list_concepts`, `describe_concept`, `search`, `validate_record`, `drift`, and opt-in `propose`

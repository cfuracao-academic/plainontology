# Changelog

## 0.1.0 (2026-11-26)

First release.

- Plain text format, version 0.1 (see SPEC.md)
- Extractors: `sqlite` (database or `.sql` file), `jsonschema`, `tools` (Anthropic, OpenAI and MCP tool definitions)
- Commands: `init`, `refresh`, `drift`, `check`, `export`, `validate`, `mcp`
- Exports: Turtle (OWL + SKOS), LinkML
- MCP server: `list_concepts`, `describe_concept`, `search`, `validate_record`, `drift`, and opt-in `propose`

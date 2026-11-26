# Changelog

## 0.2.0 (unreleased)

- New sources `postgres:` and `mysql:` (MySQL and MariaDB): schema dumps read in their own dialect (enums, arrays, `ALTER TABLE` constraints, unique indexes), or live databases read-only through SQLAlchemy
- Live database URLs come from an environment variable (`postgres:env:DATABASE_URL`); a URL with a password is refused so it is never saved in the file
- All SQL sources share one set of rules for tables, keys, foreign keys and join tables
- Irregular plurals: `people` becomes `Person`, `children` becomes `Child`
- New extras: `postgres`, `mysql`, `all`
- CI loads the example dumps into real PostgreSQL 16 and MySQL 8.4 servers and checks the live reader against the dump reader

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

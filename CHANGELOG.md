# Changelog

## 0.2.0 (unreleased)

**New sources**
- `postgres:` and `mysql:` (MySQL and MariaDB): schema dumps read in their own dialect, or live databases read-only through SQLAlchemy. Live URLs come from an environment variable (`postgres:env:DATABASE_URL`); a URL with a password is refused so it is never saved in the file. CI checks that live and dump reading give the same result on real PostgreSQL 16 and MySQL 8.4 servers
- All SQL sources now read table and column comments, CHECK constraints (as enums, `min`, `max`, `length`, `pattern`, or notes), views, triggers, non-default schemas, PostgreSQL domains and composite types, `varchar(n)` lengths, multi-column unique constraints, and join tables with extra columns
- JSON Schema: references to other files, `oneOf`/`anyOf` unions (a parent concept the options belong to, or a merged enum), and constraints (`minimum`, `maximum`, `minLength`, `maxLength`, `pattern`, `format`, exclusive bounds)

**Format 0.2** (every 0.1 file is still valid)
- Lines under a property: `label`, `synonym`, `note`, `definition`, and the rules `min`, `max`, `length`, `pattern`, `format`
- Language tags: `label "Livre"@fr`
- `unique (a, b)`

**Refresh**
- Renamed concepts and properties are recognised and your lines move with them (`>` in the report); `plainonto rename` for the cases it cannot guess
- A property that is gone from the sources but holds your lines is kept

**Validate and export**
- `validate` enforces the rules and accepts any subtype for a nested object
- New `--to shacl` export (tested with a SHACL engine)
- LinkML: mixins for further parents, `unique_keys`, rules, multilingual labels (checked with the LinkML tools in CI)
- Turtle: language tags, labels on properties, `owl:hasKey`

**Other**
- Irregular plurals: `people` becomes `Person`
- New extras: `postgres`, `mysql`, `all`

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

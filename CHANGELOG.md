# Changelog

## 0.2.0 (unreleased)

**New sources**
- `postgres:` and `mysql:` (MySQL and MariaDB): schema dumps read in their own dialect, or live databases read-only through SQLAlchemy. Live URLs come from an environment variable (`postgres:env:DATABASE_URL`); a URL with a password is refused so it is never saved in the file. CI checks that live and dump reading give the same result on real PostgreSQL 16 and MySQL 8.4 servers
- All SQL sources now read table and column comments, CHECK constraints (as enums, `min`, `max`, `length`, `pattern`, or notes), views, triggers, non-default schemas, PostgreSQL domains and composite types, `varchar(n)` lengths, multi-column unique constraints, and join tables with extra columns
- Dumps apply `ALTER TABLE` column changes (add, drop, rename, MySQL `CHANGE`/`MODIFY`) and table renames in order, so migration scripts work as a source
- JSON Schema: references to other files, `oneOf`/`anyOf` unions (a parent concept the options belong to, or a merged enum), and constraints (`minimum`, `maximum`, `minLength`, `maxLength`, `pattern`, `format`, exclusive bounds)

**Format 0.2** (every 0.1 file is still valid)
- Lines under a property: `label`, `synonym`, `note`, `definition`, and the rules `min`, `max`, `length`, `pattern`, `format`
- Language tags: `label "Livre"@fr`
- `unique (a, b)`

**Refresh**
- Renamed concepts and properties are recognised and your lines move with them (`>` in the report); `plainonto rename` for the cases it cannot guess
- A property that is gone from the sources but holds your lines is kept
- `--no-renames` on `refresh` and `drift` turns rename detection off
- A warning (`?`) when two sources define the same concept, with the number of lines that differ; before, they were combined silently

**Validate and export**
- `validate` enforces the rules and accepts any subtype for a nested object
- New `--to shacl` export (tested with a SHACL engine)
- LinkML: mixins for further parents, `unique_keys`, rules, multilingual labels (checked with the LinkML tools in CI)
- Turtle: language tags, labels on properties, `owl:hasKey`

**Fixes from an independent review before release**
- Nothing PlainOntology writes can be unreadable: numbers never use exponents, every line-separator and control character in a string is escaped, database regexes are translated to Python (`\y`, POSIX classes) or kept as a note, names may start with a letter in any script, and a final check refuses to write a file that would not read back
- A person's lines are written back exactly as typed, including spacing and backslashes; unknown escapes such as `\d` are kept, and blank lines and the header order are kept
- Enum values with `|`, brackets or quotes are written quoted: `enum(S|"M (medium)"|"L|XL")`
- A property or concept gone from the sources but holding your lines stays `[auto]` and is reported on every run; a concept header you wrote stays yours; lines pointing to a renamed concept are listed, and `plainonto rename` updates them
- UTF-8 byte order marks are accepted in every source and in `.onto` files; a source that produces nothing, a skipped statement, and two tables with the same concept name are warnings (the second table gets its own name)
- Security: passwords in URL query strings are refused; SQLite `.sql` sources cannot attach or write other files
- Every failure exits 2, never 1; the Action keeps error output out of the report, keeps one pull request comment up to date and skips forks; the drift hook runs on every commit; `drift` and `check` without files check every `.onto` file
- MCP `propose` refuses lines that would break the file and can propose under a property (`Concept.property`)
- `validate` treats `key` as required and checks dates strictly on every Python version; dbt date ranges and exclusive ranges become notes; LinkML enum and prefix names no longer collide
- CI builds the wheel, installs it in clean environments and uses it

 (Pagila and Sakila, in CI at pinned versions), which led to:
- PostgreSQL partitions fold into their parent table with a note, and foreign keys declared on partitions carry up
- PostgreSQL 18 virtual generated columns are read
- A table the parser rejects is read column by column; anything skipped is reported as a `?` warning instead of disappearing
- Function bodies with `$1` placeholders no longer crash the dump reader

**More sources**
- `openapi:` (OpenAPI 3.x, Swagger 2.0; JSON or YAML): schemas become concepts, operations become concepts with parameters, body and `returns`
- `dbt:` (manifest or project folder): models, seeds, snapshots and sources; tests and constraints carry over as flags, enums, relations and rules; lineage and semantic-layer measures as notes

**Tooling**
- GitHub Action (`uses: cfuracao-academic/plainontology@v0.2.0`): drift as a check, job summary, optional pull request comment
- pre-commit hooks `plainonto-check` and `plainonto-drift`; `check` and `drift` take several files
- VS Code extension for `.onto` syntax highlighting (`editors/vscode`)
- Documentation site (MkDocs) with a tutorial, sources, CI, agents, performance and the file format
- Benchmark script; 5,000 tables extract in about 7 seconds
- ruff, mypy and a coverage floor in CI; code of conduct, security policy, issue and pull request templates
- The file format has a stated versioning policy (SPEC.md)

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

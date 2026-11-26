# PlainOntology

[![CI](https://github.com/cfuracao-academic/plainontology/actions/workflows/ci.yml/badge.svg)](https://github.com/cfuracao-academic/plainontology/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/plainontology)](https://pypi.org/project/plainontology/)
[![Python](https://img.shields.io/badge/python-3.10%20to%203.13-blue)](pyproject.toml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24562420.svg)](https://doi.org/10.5281/zenodo.24562420)

**Write down the ontology your system already has, as one plain text file that people can edit and AI agents can read.**

**Try it:** `pip install plainontology`, then `plainonto init my.onto --from sqlite:my.db`

![Demo: PlainOntology extracts a schema, a person adds definitions and synonyms, the database changes, drift is reported, and refresh keeps the person's lines](https://raw.githubusercontent.com/cfuracao-academic/plainontology/main/docs/demo.gif)

Every running system carries an implicit ontology: tables and foreign keys, JSON schemas, the tool definitions an LLM agent calls. Its meaning (what a "loan" is, that a "Title" is a "Book", that `member` holds personal data) usually lives in people's heads. PlainOntology extracts the structure into a readable text file, keeps your hand-written meaning next to it, and tells you when the system drifts away from it.

```text
concept Book [auto: postgres table books]
  definition "A title the library holds; one record per edition." [auto]
  has id : integer key [auto]
  has title : text required [auto]
  has isbn : text unique "ISBN-13 without hyphens." [auto]
    length ..13 [auto]
    label "ISBN"
  has pages : integer [auto]
    min 1 [auto]
  author -> Author required [auto: fk books.author_id]
  tags -> Tag many [auto: via book_tags]
  label "Livre"@fr
  synonym "Title"
  same_as schema:Book
```

The definition and the description come from comments in the database, `length ..13` from `varchar(13)`, and `min 1` from `CHECK (pages > 0)`.

Lines tagged `[auto]` come from the sources and are refreshed. **Every other line is yours and is never overwritten.**

## What it does

| | |
|---|---|
| **Extract** | PostgreSQL, MySQL and SQLite (live databases, dumps or migration scripts), JSON Schema (draft 7 and 2020-12, across files), OpenAPI 3.x and Swagger 2.0, dbt manifests, and AI agent tool definitions (Anthropic, OpenAI Chat and Responses, MCP `tools/list`) |
| **Keep your edits** | Refresh replaces `[auto]` lines only. If you rewrite an extracted line, your version wins and is reported |
| **Follow renames** | A renamed table or column keeps your lines; `plainonto rename` handles the cases it cannot guess, and `--no-renames` turns the guessing off |
| **Report drift** | `plainonto drift` exits 1 when the sources changed; a GitHub Action and pre-commit hooks put it in CI. It also warns when two sources define the same concept |
| **Check** | Duplicate lines, unknown targets, undeclared prefixes, misplaced rules, proposals waiting for review |
| **Validate** | Check JSON records (for example an agent's structured output) against a concept: types, enums, required fields, min, max, length, pattern, format |
| **Multilingual** | Labels, definitions and synonyms in any language (`label "Livre"@fr`) |
| **Export** | RDF Turtle (OWL structure, SKOS meaning), SHACL shapes for validating RDF data, and LinkML YAML |
| **Serve to agents** | An MCP server so agents look concepts up, validate their output, and propose new lines for a person to review |

The core has no dependencies beyond the Python standard library. Extras: `mcp` for the MCP server, `postgres` and `mysql` for those databases, `openapi` for YAML OpenAPI files, `all` for everything.

**Documentation:** [tutorial, sources, CI, agents and the file format](docs/index.md) (also built as a site with `mkdocs serve`).

## Quick start

```bash
pip install "plainontology[all]"     # or just [mcp], [postgres], [mysql], [openapi]

cd examples
plainonto init library.onto --from sqlite:library.sql --from tools:agent-tools.json
# edit library.onto: add definitions, synonyms, same_as mappings
plainonto refresh library.onto       # re-extract; your lines stay
plainonto drift library.onto         # report only; exit 1 on drift
plainonto check library.onto
plainonto export library.onto --to turtle -o library.ttl
plainonto export library.onto --to shacl -o library.shapes.ttl
plainonto export library.onto --to linkml -o library.yaml
plainonto validate library.onto Loan record.json
plainonto rename library.onto Loan Borrowing   # or Concept.old Concept.new for a property
```

The command is `plainonto` (short for `plainontology`; both work).

Sources are written `KIND:LOCATION` and stored in the file; file paths are relative to it:

| Kind | Location |
|---|---|
| `postgres` | a schema dump (`pg_dump --schema-only`), or a live database: `env:VARIABLE` holding the URL |
| `mysql` | a schema dump (`mysqldump --no-data`), or a live MySQL or MariaDB database: `env:VARIABLE` |
| `sqlite` | a SQLite database, or a SQLite-compatible `.sql` file (loaded into memory) |
| `jsonschema` | a JSON Schema file; `$defs` and nested objects become concepts. For Pydantic, dump `Model.model_json_schema()` first |
| `openapi` | an OpenAPI 3.x or Swagger 2.0 file (JSON, or YAML with the `openapi` extra): schemas become concepts, operations become concepts with their parameters, body and `returns` |
| `dbt` | a dbt manifest (`target/manifest.json`) or the project folder: models and sources become concepts; `not_null`, `unique`, `accepted_values`, `relationships` and `accepted_range` tests carry over; lineage and semantic-layer measures become notes |
| `tools` | a JSON list of tool definitions, or an object with a `tools` list |

**Databases.** Dumps are read in their own dialect, and live databases through SQLAlchemy, read-only. Both give the same result (CI checks this against real PostgreSQL 16 and MySQL 8.4 servers):

| From the database | In the file |
|---|---|
| tables, views | concepts (views get a note); tables outside `public` keep their schema: `archive.orders` becomes `ArchiveOrder` |
| table and column comments | the concept's definition, the property's description |
| primary and foreign keys, unique constraints | `key`, relations, `unique`, `unique (a, b)` |
| `CHECK (x IN (...))`, `CHECK (x >= 0)`, `CHECK (x ~ 'regex')`, `varchar(n)` | an enum, `min`, `pattern`, `length ..n`; other checks become notes |
| enums, domains, arrays, `tinyint(1)` | `enum(...)`, the domain's type and rules, `many`, `boolean` |
| join tables | a `many` relation; a join table with extra columns also stays a concept |
| triggers | notes |
| `ALTER TABLE` (add, drop, rename and change columns, rename tables) | applied in order, so a folder of migration scripts concatenated into one file works as a dump |
| partitions | fold into the parent table with a note; foreign keys declared on partitions carry up |
| anything the parser cannot read | reported as a `?` warning, never dropped silently; a table is read column by column so one odd column cannot hide it |

Everything else in a dump (`SET`, data, functions) is skipped. For a live database:

```bash
export LIBRARY_DB="postgresql://reader@db.internal/library"   # password via ~/.pgpass, or in this variable
plainonto init library.onto --from postgres:env:LIBRARY_DB
```

A URL with a password is refused, because the source line is saved in the ontology file.

## Use it in CI and git hooks

```yaml
# .github/workflows/ontology.yml
on: [pull_request]
permissions: { contents: read, pull-requests: write }
jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: cfuracao-academic/plainontology@v0.2.0
        with:
          file: ontology/library.onto
          install: plainontology[postgres]
          comment: "true"          # post the drift report on the pull request
```

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/cfuracao-academic/plainontology
    rev: v0.2.0
    hooks:
      - id: plainonto-check
      - id: plainonto-drift
```

All inputs are in [docs/ci.md](docs/ci.md). Editors: a [VS Code extension](editors/vscode) highlights `.onto` files.

## Tried on real schemas

CI extracts two public sample databases at pinned versions on every push:

| Schema | Result |
|---|---|
| [Pagila](https://github.com/devrimgunduz/pagila) (PostgreSQL) | 24 concepts from 71 tables: 55 monthly partitions fold into `Payment`, foreign keys declared on partitions carry up, a PostgreSQL 18 virtual column is read; no warnings, `check --strict` clean |
| [Sakila](https://github.com/jOOQ/sakila) (MySQL) | 23 concepts (16 tables, 7 views); no warnings, `check --strict` clean |

Each takes about 0.4 seconds.

## Performance

1,000 tables (13,500 lines) extract or refresh in about 1.5 seconds; 5,000 tables in about 7; `check` and the exports take under a second. Time grows linearly. Details and the script in [docs/performance.md](docs/performance.md).

## Use it from an AI agent (MCP)

```bash
plainonto mcp path/to/library.onto                    # read-only tools
plainonto mcp path/to/library.onto --allow-proposals  # also lets agents propose lines
```

Claude Code:

```bash
claude mcp add plainontology -- plainonto mcp /absolute/path/to/library.onto
```

Claude Desktop or any MCP client (`mcpServers` in the client's config):

```json
{
  "mcpServers": {
    "plainontology": { "command": "plainonto", "args": ["mcp", "/absolute/path/to/library.onto"] }
  }
}
```

| Tool | What it does |
|---|---|
| `list_concepts` | every concept with its definition |
| `describe_concept` | a concept's lines, inherited properties and who points to it |
| `search` | find concepts by name, label, synonym, definition or property |
| `validate_record` | check a JSON object against a concept before acting on it |
| `drift` | compare the file with its sources now |
| `propose` | only with `--allow-proposals`: add a line tagged `[proposed]`; a person accepts it by deleting the tag |

The file is re-read on every call, so edits show up at once. Agents can never change or delete an existing line.

## Why a text file

* **Diffable.** Ontology changes go through pull requests like code.
* **Two layers in one place.** Structure (`has`, `->`, `is_a`) and meaning (`label`, `definition`, `synonym`, `same_as`, `note`) sit together, so neither drifts from the other.
* **Interoperable.** Export to OWL/SKOS and LinkML when you need the standard toolchains.

## How it compares

| | PlainOntology | LinkML | OWL editors (Protégé) | Data catalogues (DataHub, OpenMetadata) |
|---|---|---|---|---|
| Direction | extracts from a running system | you write the schema, it generates | you write the ontology | ingests metadata |
| Format | one plain text file | YAML | RDF/OWL | database behind a server |
| Keeps hand edits across re-extraction | yes | n/a | n/a | partly |
| LLM tool schemas and MCP | yes | no | no | no |
| Setup | `pip install` | `pip install` | desktop app | servers |

PlainOntology does not replace these. It exports to LinkML and OWL so you can move to them when you need more.

## Format

The full grammar and refresh rules are in [SPEC.md](SPEC.md). In short:

```text
prefix : <https://example.org/library#>
source sqlite:library.sql

concept NAME [auto: where it came from]
  has PROPERTY : TYPE [FLAGS] ["description"]
    label "..."            (also synonym, note: about the property)
    min N                  (also max N, length LOW..HIGH, pattern "regex", format email)
  PROPERTY -> CONCEPT [FLAGS] ["description"]
  is_a CONCEPT
  unique (PROPERTY, PROPERTY)
  label "..."@fr           (also definition, synonym, note; language tag optional)
  same_as prefix:Term
```

Types: `text integer decimal boolean date datetime binary json any enum(a|b)`. Flags: `key required unique many`. The empty prefix line sets the base IRI for exports.

## Known limitations

* Concept names are made with English singular rules (`books` becomes `Book`, `people` becomes `Person`).
* The MCP server searches by keyword, runs locally over stdio, and does not lock the file while an agent proposes a line.
* JSON Schema references to web URLs are not fetched; `plainonto check` flags the missing concept.
* CHECK constraints more complex than a list, a comparison, a range or a regex are kept as notes, not enforced by `validate`.
* Views are read for their columns and types; the meaning of the query itself is not.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Citing

If you use PlainOntology in research, please cite it using [CITATION.cff](CITATION.cff). The DOI [10.5281/zenodo.24562420](https://doi.org/10.5281/zenodo.24562420) always points to the latest version; each release also has its own DOI on Zenodo.

## Licence

MIT

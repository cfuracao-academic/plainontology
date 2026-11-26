# Sources

A source is written `KIND:LOCATION` and saved in the file (file paths relative to it). One file can have several sources; when two define the same concept, the report warns and the first source wins where they disagree.

| Kind | Location | Install |
|---|---|---|
| `sqlite` | a SQLite database, or a SQLite-compatible `.sql` file | included |
| `postgres` | a dump (`pg_dump --schema-only`) or migration scripts, or a live database: `env:VARIABLE` | `plainontology[postgres]` |
| `mysql` | the same for MySQL and MariaDB (`mysqldump --no-data`) | `plainontology[mysql]` |
| `jsonschema` | a JSON Schema file, draft 7 or 2020-12 | included |
| `openapi` | an OpenAPI 3.x or Swagger 2.0 file, JSON or YAML | `plainontology[openapi]` for YAML |
| `dbt` | a dbt manifest (`target/manifest.json`) or the project folder | included |
| `tools` | AI agent tool definitions (Anthropic, OpenAI, MCP `tools/list`) | included |

## Databases

Dumps are read in their own dialect; live databases through SQLAlchemy, read-only. Both give the same result, which CI checks against real PostgreSQL 16 and MySQL 8.4 servers.

| From the database | In the file |
|---|---|
| tables, views | concepts (views get a note); tables outside `public` keep their schema: `archive.orders` becomes `ArchiveOrder` |
| table and column comments | the concept's definition, the property's description |
| primary and foreign keys, unique constraints | `key`, relations, `unique`, `unique (a, b)` |
| `CHECK (x IN (...))`, `CHECK (x >= 0)`, `CHECK (x ~ 'regex')`, `varchar(n)` | an enum, `min`, `pattern`, `length ..n`; other checks become notes |
| enums, domains, arrays, `tinyint(1)` | `enum(...)`, the domain's type, its CHECKs and NOT NULL, `many`, `boolean` |
| `LIKE other`, `CREATE TABLE ... AS SELECT`, `INHERITS (parent)` | the copied columns (and keys and checks with `INCLUDING`); the query's columns; `is_a Parent` |
| join tables | a `many` relation; a join table with extra columns also stays a concept |
| triggers | notes |
| `ALTER TABLE` (add, drop, rename, change and alter columns; drop constraints; rename tables), `DROP TABLE` | applied in order, so migration scripts work as a dump; a constraint that cannot be found is a warning |
| several CHECKs on one column | the strictest `min` and `max`; every pattern is kept and all must match |
| partitions (`PARTITION OF`, `ATTACH PARTITION`) | fold into the parent table with a note; their foreign keys carry up |
| a statement or column the parser cannot read | skipped and reported as a `?` warning, never dropped silently; a table is read column by column so one odd column does not hide it |

For a live database, keep the URL in an environment variable. A URL with a password in the source line is refused, because the line is saved in the file.

```bash
export LIBRARY_DB="postgresql://reader@db.internal/library"
plainonto init library.onto --from postgres:env:LIBRARY_DB
```

## JSON Schema and OpenAPI

Objects become concepts; nested objects become concepts of their own. References work within a file and across files (web URLs are not fetched). `oneOf`/`anyOf` with several object options becomes a parent concept the options belong to. `minimum`, `maximum`, `minLength`, `maxLength`, `pattern` and `format` become rules.

OpenAPI adds one concept per operation (`is_a Operation`), with its parameters and body as properties and `returns` pointing to the success response.

## dbt

Models, seeds, snapshots and sources become concepts. Tests carry over: `not_null` gives `required`, `unique` gives `unique`, `accepted_values` an enum, `relationships` a relation, `dbt_utils.accepted_range` min and max; a `primary_key` constraint gives `key`. Lineage becomes a "built from" note, and semantic models add notes for measures and dimensions.

## Tools

Each tool becomes a concept that `is_a Tool`; its input parameters are its properties.

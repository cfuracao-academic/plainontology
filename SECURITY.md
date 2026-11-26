# Security

## Supported versions

Only the latest release on PyPI gets security fixes.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead: on the repository's **Security** tab, choose **Report a vulnerability**. You will get an answer within seven days.

## What PlainOntology touches

* **Live databases** are read with read-only schema queries (no data is read, nothing is written). Connection URLs come from environment variables; a URL that contains a password is refused, because source lines are saved in the ontology file.
* **Dump files and schemas** are parsed, never executed, except SQLite `.sql` files, which are loaded into a temporary in-memory database. Only use `.sql` files you trust as SQLite sources.
* **JSON Schema references** are followed to local files only. Web URLs are never fetched.
* **The MCP server** runs locally over stdio. By default agents can only read; with `--allow-proposals` they can add lines tagged `[proposed]` to the file, never change or delete existing ones.

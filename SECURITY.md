# Security

## Supported versions

Only the latest release on PyPI gets security fixes.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead: on the repository's **Security** tab, choose **Report a vulnerability**. You will get an answer within seven days.

## What PlainOntology touches

* **Live databases** are read with read-only schema queries (no data is read, nothing is written). Connection URLs come from environment variables; a URL that contains a password is refused, because source lines are saved in the ontology file.
* **Dump files and schemas** are parsed, never executed, except SQLite `.sql` files, which run in a temporary in-memory database that may not attach, open or write any other database file (`ATTACH`, `VACUUM INTO` and file functions are refused).
* **Connection URLs** in a source line may not carry a password, in the user part or in the query string (`password`, `sslpassword`, `pwd` and similar); the check does not depend on optional packages.
* **Running on untrusted input** (a pull request's files in CI): set `PLAINONTO_NO_LIVE=1` to refuse live database sources and `PLAINONTO_SOURCE_ROOT` to a folder that every source file must be inside. The GitHub Action sets both by default. A `.sql` SQLite source that runs longer than 10 seconds is stopped.
* **JSON Schema references** are followed to local files only (inside `PLAINONTO_SOURCE_ROOT` when set). Web URLs are never fetched.
* **The MCP server** runs locally over stdio. By default agents can only read; with `--allow-proposals` they can add lines tagged `[proposed]` to the file, never change or delete existing ones.

# Roadmap

## 0.1 (this release)

- [x] Plain text format with `[auto]`, `[proposed]` and hand-written lines
- [x] Extractors: SQLite and `.sql`, JSON Schema, LLM tool definitions
- [x] Refresh that keeps hand-written lines, and drift reports for CI
- [x] Check and record validation
- [x] Export to Turtle (OWL + SKOS) and LinkML
- [x] MCP server with opt-in proposals

## Next

- [x] PostgreSQL and MySQL extractors: dumps and live databases (0.2.0)
- [x] Read SQL table and column comments into definitions and descriptions (0.2.0)
- [x] Recognise renamed tables and columns (0.2.0)
- [x] SHACL export, rules, language tags, JSON Schema across files (0.2.0)
- [ ] Mermaid diagram export
- [x] OpenAPI extractor (components/schemas and operations) (0.2.0)
- [ ] Pydantic extractor without dumping JSON Schema first
- [ ] RAG metadata extractor (field names and values in a vector store's payloads)
- [x] GitHub Action: `plainonto drift` on pull requests, with the report as a comment (0.2.0)
- [x] dbt extractor, pre-commit hooks, VS Code syntax highlighting, docs site (0.2.0)
- [ ] Results on public sample databases (Pagila, Sakila, Chinook) in the docs
- [ ] Publish the Action on the GitHub Marketplace and the VS Code extension on the VS Code Marketplace
- [ ] Import from Turtle/SKOS so an existing vocabulary can seed the meaning layer
- [ ] `plainonto diff A.onto B.onto` between two files or two commits

## Community and visibility

- [x] README demo GIF: extract, edit, refresh, your edit survives (`docs/demo/make_demo.py`)
- [ ] Hugging Face Space: paste a schema, get the file
- [ ] Hugging Face dataset: PlainOntology files extracted from public OpenAPI specs
- [x] Zenodo DOI for each release
- [ ] Demo paper (ISWC, ESWC or SEMANTiCS demo track)

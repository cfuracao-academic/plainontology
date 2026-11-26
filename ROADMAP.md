# Roadmap

## 0.1 (this release)

- [x] Plain text format with `[auto]`, `[proposed]` and hand-written lines
- [x] Extractors: SQLite and `.sql`, JSON Schema, LLM tool definitions
- [x] Refresh that keeps hand-written lines, and drift reports for CI
- [x] Check and record validation
- [x] Export to Turtle (OWL + SKOS) and LinkML
- [x] MCP server with opt-in proposals

## Next

- [ ] PostgreSQL and MySQL extractors (via SQLAlchemy inspection)
- [ ] OpenAPI extractor (components/schemas and operations)
- [ ] Pydantic extractor without dumping JSON Schema first
- [ ] RAG metadata extractor (field names and values in a vector store's payloads)
- [ ] GitHub Action: `plainonto drift` on pull requests, with the report as a comment
- [ ] Import from Turtle/SKOS so an existing vocabulary can seed the meaning layer
- [ ] `plainonto diff A.onto B.onto` between two files or two commits

## Community and visibility

- [ ] README demo GIF: extract, edit, refresh, your edit survives
- [ ] Hugging Face Space: paste a schema, get the file
- [ ] Hugging Face dataset: PlainOntology files extracted from public OpenAPI specs
- [ ] Zenodo DOI for each release
- [ ] Demo paper (ISWC, ESWC or SEMANTiCS demo track)

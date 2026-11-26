# With AI agents

`plainonto mcp FILE` serves the file over the Model Context Protocol (stdio). Install with `pip install "plainontology[mcp]"`.

```bash
claude mcp add plainontology -- plainonto mcp /absolute/path/to/library.onto
```

Any MCP client:

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
| `validate_record` | check a JSON object against a concept before acting on it (types, enums, rules, nested objects) |
| `drift` | compare the file with its sources now |
| `propose` | only with `--allow-proposals`: add a line tagged `[proposed]`; a person accepts it by deleting the tag |

The file is re-read on every call, so edits show up at once. Agents can never change or delete an existing line.

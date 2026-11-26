# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Set up

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip
pytest
```

Before opening a pull request, run the same checks as CI:

```bash
ruff check src tests && ruff format --check src tests && mypy && pytest --cov --cov-fail-under=85
```

## Ground rules

1. **Never overwrite a person's line.** Any change to merge must keep the refresh rules in [SPEC.md](SPEC.md) and the tests in `tests/test_merge.py` passing.
2. **Keep the core dependency-free.** Only the standard library in `src/plainontology`, except `mcp_server.py`. New extractors that need a library go behind an optional extra.
3. **Format changes need a spec change.** If the text format changes, update SPEC.md in the same pull request.
4. **Test with public data only.** Examples and fixtures must be public or made up.

## Adding an extractor

1. Add `src/plainontology/extract/<kind>.py` with a function `extract_<kind>(path: Path) -> list[Concept]`. For a new SQL dialect, read it into `Table` objects instead and call `tables_to_concepts` from `sqlmodel.py`, so every SQL source follows the same rules.
2. Tag every concept and statement it produces with `Tag("auto", ...)`.
3. Register it in `EXTRACTORS` in `src/plainontology/extract/__init__.py` (wrap file-based extractors with `_file`).
4. Add an example under `examples/` and tests under `tests/`. If it can read a live system, add a CI job that checks live and file reading agree, like `live-databases` in `.github/workflows/ci.yml`.

## Demo GIF

`docs/demo.gif` is drawn from real command output by `docs/demo/make_demo.py` (needs `pip install pillow`). Re-run it after changing commands or output.

## Releasing

1. Set the new version in `pyproject.toml`, `src/plainontology/__init__.py` and `CITATION.cff` (a test checks they match), and add a CHANGELOG entry.
2. Commit and push to `main`; wait for CI to pass.
3. Create a GitHub release with the tag `vX.Y.Z`. The release workflow builds the package and uploads it to PyPI, and Zenodo mints a DOI for the release.

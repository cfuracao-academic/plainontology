# In CI and git hooks

## GitHub Action

```yaml
name: Ontology
on: [pull_request]
permissions:
  contents: read
  pull-requests: write   # only for comment: "true"
jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: cfuracao-academic/plainontology@v0.2.0
        with:
          file: ontology/library.onto
          install: plainontology[postgres]   # extras for your sources
          comment: "true"                     # post the report on the pull request
```

| Input | Default | Meaning |
|---|---|---|
| `file` | | the `.onto` file, or several separated by spaces |
| `install` | `plainontology` | what to `pip install`, with extras or a version |
| `fail-on-drift` | `true` | fail the step when the sources changed |
| `comment` | `false` | post the report as a pull request comment |
| `no-renames` | `false` | never treat a vanished concept or property as renamed |
| `python-version` | `3.12` | |

Outputs: `drift` (`"true"` or `"false"`) and `report`. The report also appears in the job summary. With `comment: "true"` one comment is kept up to date on the pull request; pull requests from forks are skipped (their token is read-only) and a failed comment never fails the check.

For a live database source, pass the URL as a secret: `env: LIBRARY_DB: ${{ secrets.LIBRARY_DB }}` on the step.

## pre-commit

```yaml
repos:
  - repo: https://github.com/cfuracao-academic/plainontology
    rev: v0.2.0
    hooks:
      - id: plainonto-check
      - id: plainonto-drift
        additional_dependencies: ["plainontology[postgres]"]
```

`plainonto-check` catches mistakes in hand edits. `plainonto-drift` runs on every commit (not only when an `.onto` file changes), so a commit that only changes a schema is stopped too; it checks every `.onto` file in the repository.

## Anywhere else

`plainonto drift FILE` exits 0 when the file matches its sources, 1 on drift, and 2 when a file or source cannot be read or anything else fails (never 1, so a failure is never mistaken for drift). Without a file it checks every `.onto` file under the current folder.

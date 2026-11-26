# Tutorial

This walks through the library example in the repository's `examples` folder. Clone the repository, or copy `examples/library.sql` and `examples/agent-tools.json` into an empty folder.

## 1. Extract

```bash
pip install plainontology
plainonto init library.onto --from sqlite:library.sql --from tools:agent-tools.json
```

`library.onto` now holds one concept per table and per tool. Every line ends in `[auto]`: it came from a source.

```text
concept Book [auto: sqlite table books]
  has id : integer key [auto]
  has title : text required [auto]
  has isbn : text unique [auto]
    length ..13 [auto]
  author -> Author required [auto: fk books.author_id]
  tags -> Tag many [auto: via book_tags]
```

## 2. Add meaning

Open the file in any editor and add lines without a tag. They are yours.

```text
  definition "A title the library holds; one record per edition."
  synonym "Title"
  label "Livre"@fr
  same_as schema:Book
```

Under a property you can describe the property itself, and add rules:

```text
  has isbn : text unique [auto]
    length ..13 [auto]
    label "ISBN"
    pattern "^[0-9]{13}$"
```

Run `plainonto check library.onto` to catch typos, duplicate lines and rules in the wrong place.

## 3. Let the system change

Add a column to `library.sql`, then:

```bash
plainonto drift library.onto      # reports + Book: has pages : integer, and exits 1
plainonto refresh library.onto    # writes the change; your lines stay where they are
```

If a table or column is renamed, refresh moves your lines to the new name when it can tell, and lists any of your lines elsewhere that still point to the old name. When it cannot tell, use `plainonto rename library.onto Member Patron` before refreshing. `--no-renames` turns the guessing off. To give an extracted concept a friendlier name, add a `label` instead: the concept name follows the source.

## 4. Use the file

```bash
plainonto validate library.onto Loan record.json             # check JSON records against a concept
plainonto export library.onto --to turtle -o library.ttl     # OWL + SKOS
plainonto export library.onto --to shacl -o library.shapes.ttl
plainonto export library.onto --to linkml -o library.yaml
plainonto mcp library.onto                                    # serve it to AI agents
```

## 5. Keep it honest

Commit `library.onto` next to your code and add the [GitHub Action or pre-commit hook](ci.md): a pull request that changes the schema without refreshing the file fails the check.

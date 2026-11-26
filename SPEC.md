# PlainOntology file format, version 0.2

A PlainOntology file is UTF-8 plain text, one statement per line. Files conventionally end in `.onto`. Version 0.2 adds lines under properties, language tags, `unique (...)` and validation rules; every 0.1 file is a valid 0.2 file.

## Versioning

* The format has its own version, written in the header comment that `plainonto init` creates (`# PlainOntology file, format 0.2`). It changes only when the grammar or the refresh rules change, not with every release of the tool.
* **Within 0.x, format versions only add.** A new version may add statements, rules, flags or types; it does not remove or change the meaning of existing ones. Every file written for an older format stays valid and means the same.
* An older PlainOntology that meets newer syntax stops with the line number, rather than guessing.
* **1.0 freezes the grammar.** After 1.0, changes that would make an existing file invalid or change its meaning need a new major version and a migration command.
* The Python package follows [semantic versioning](https://semver.org); command-line changes that could break scripts are listed in CHANGELOG.md.

## File layout

```text
# comments before the first concept are the file header
prefix : <https://example.org/library#>
prefix ex: <https://example.org/terms/>
source sqlite:library.sql
source tools:agent-tools.json

# a comment at the start of a line, before a concept, belongs to that concept
concept Book [auto: sqlite table books]
  has title : text required [auto]
  has isbn : text unique "ISBN-13 without hyphens." [auto]
    label "ISBN"
    length 13..13 [auto]
  # an indented comment stays inside the concept
  definition "A title the library holds."
  label "Livre"@fr
  unique (title, isbn)
```

* Blank lines are ignored.
* `prefix` and `source` lines must come before the first concept.
* `prefix : <IRI>` (empty prefix) sets the base IRI used by exports.
* These prefixes are known without declaring them: `owl rdf rdfs xsd skos sh schema dcterms foaf prov`.
* A statement line is indented (spaces or tabs) and belongs to the concept above it.
* A line indented further than a property line (`has` or `->`) belongs to that property.

## Concepts

```text
concept NAME [TAG]
```

`NAME` starts with a letter (in any script: `Élan`, `日本` are fine) or `_`, and may contain letters, digits, `_ . : / -`. Each name appears once per file.

## Statements on a concept

| Statement | Meaning | Layer |
|---|---|---|
| `has NAME : TYPE [FLAGS] ["description"]` | an attribute with a datatype | structure |
| `NAME -> CONCEPT [FLAGS] ["description"]` | a relation to another concept (or a `prefix:Term`) | structure |
| `is_a CONCEPT` | subclass of (a concept may have several) | structure |
| `unique (a, b)` | these properties together identify one record | structure |
| `label "text"[@lang]` | preferred human name | meaning |
| `definition "text"[@lang]` | what the concept is | meaning |
| `synonym "text"[@lang]` | another name people use | meaning |
| `same_as prefix:Term` | the same concept in another vocabulary | meaning |
| `note "text"[@lang]` | anything else worth knowing | meaning |
| `# text` | a comment | |

**Types:** `text`, `integer`, `decimal`, `boolean`, `date`, `datetime`, `binary`, `json`, `any`, or `enum(a|b|c)`. An enum value that contains `|`, a bracket, a quote, or spaces at either end is written as a quoted string: `enum(S|"M (medium)"|"L|XL")`.

**Flags:** `key` (identifier), `required`, `unique`, `many` (list or to-many relation).

**Strings** are double-quoted. Inside them `\"` is a quote, `\\` a backslash, `\n` a new line, `\r` a carriage return, `\t` a tab and `\uXXXX` any other character (PlainOntology writes line separators and control characters this way, so a string never breaks a line). Any other backslash is kept as written, so `pattern "^\d{4}$"` means what it says.

**Numbers** in `min` and `max` are plain decimals (`0.00001`, never `1e-05`).

**Language tags** follow BCP 47 (`@fr`, `@pt-BR`). A string without a tag is the default language. One `label` and one `definition` per language.

## Lines under a property

| Line | Meaning |
|---|---|
| `label "text"[@lang]`, `synonym ...`, `note ...`, `definition ...` | meaning of the property itself; the description on the property line is its default definition |
| `min N`, `max N` | inclusive bounds for numbers |
| `length LOW..HIGH` | string length; either bound may be left out (`..13`, `1..`) |
| `pattern "regex"` | the value must contain a match (anchor with `^...$` for a full match); a property may have several, and all must match. `$` matches only at the very end |
| `format NAME` | a JSON Schema format: `email`, `uri`, `uuid`, `hostname`, `ipv4`, `ipv6` are checked by `validate` |

Rules apply to each value; for a `many` property, to each item. Rules are not allowed on a concept or under a relation.

## Tags

A tag at the end of a concept or statement line says who owns it:

| Tag | Owner | On refresh |
|---|---|---|
| `[auto]` or `[auto: detail]` | the sources | replaced by what the sources say now |
| `[proposed]` or `[proposed: detail]` | an agent, waiting for review | kept; accept by deleting the tag |
| no tag | a person | always kept |

The detail after `auto:` records where a line came from, for example `fk books.author_id`.

## Identity of a line

Merge matches lines by key, one line per key per concept (or per property, for lines under a property):

| Statement | Key |
|---|---|
| `has` and `->` | the property name (they share one namespace) |
| `label`, `definition` | the statement kind and language |
| `synonym`, `note` | the text and language |
| `is_a`, `same_as` | the target |
| `unique (...)` | the set of properties, in any order |
| `min`, `max`, `length`, `format` | the rule kind (one each per property) |
| `pattern` | the regex (several per property) |
| comment | the text |

## Refresh rules

When `plainonto refresh` re-extracts the sources:

1. Lines without a tag and `[proposed]` lines are kept, in place.
2. Each `[auto]` line is replaced by the extracted line with the same key, or removed if the sources no longer produce it. New extracted lines are appended to their concept (or property).
3. If a person's line has the same key as an extracted line, the person's line wins and the extracted line is not written. The drift report shows both (`=`).
4. Lines under a property follow the same rules. If you rewrote a property line, extracted rules still arrive under your line.
5. A concept or property that is gone from the sources is removed if it was extracted and holds none of your lines. If it holds your lines, it is kept, still tagged `[auto]`, and reported (`!`) on every run, so CI keeps flagging it until you move or delete your lines (or the source comes back, and it refreshes as usual).
6. **Ownership of a concept.** A concept header you wrote (no tag) or an agent proposed stays as it is, even when a source starts producing a concept of that name; the extracted lines still arrive inside it.
7. **Renames.** If an extracted concept holding your lines disappears and exactly one new concept appears with at least 60% of the same properties (and at least two in common), it is a rename: your lines move to the new name, in the old place (`>`). If an extracted property holding your lines disappears and exactly one new property of the same kind and type appears in that concept, your lines move to it (`>`). Anything less certain is reported, not guessed; use `plainonto rename` for those. `--no-renames` on `refresh` and `drift` turns rename detection off. Your lines elsewhere that point to the old name (`about -> Member`) are never rewritten; the report lists them (`?`) and `plainonto rename FILE Member Patron` updates them.
8. If two sources (or two tables of one source) would make the same concept, the report warns (`?`). Within one SQL source the second table gets a distinct name (`books` becomes `Books` next to `Book`); across sources the lines are combined and the first source wins. Statements a source cannot read, and a source that produces nothing, are warnings too. A warning is not drift.
9. **Your text is kept.** A line you wrote is written back exactly as you typed it, spacing and escapes included, and so are header lines, comments, blank lines and the file's line endings. A file without a header gets none added. Indentation of statement lines is normalised to two spaces per level.
10. **Proposals wait.** `[proposed]` lines and concepts do not count in `validate` or the exports until a person accepts them by deleting the tag.
11. Refresh never writes a file it cannot read back, and is idempotent: running it twice changes nothing the second time.

## Drift report symbols

| Symbol | Meaning |
|---|---|
| `+` | added from the sources |
| `-` | gone from the sources |
| `~` | changed in the sources |
| `>` | renamed in the sources; your lines moved with it |
| `=` | your line kept over a different extracted line (not counted as drift) |
| `!` | gone from the sources, kept because it holds your lines |
| `?` | warning: two sources or tables make the same concept, a statement could not be read, a source produced nothing, or a line of yours points to a renamed concept (not counted as drift) |

## Exports

**Turtle (OWL + SKOS):** each concept is an `owl:Class`; `has` lines become `owl:DatatypeProperty` and relations `owl:ObjectProperty`, scoped as `Concept/property`. Single-valued properties are also `owl:FunctionalProperty`; `required` and `key` add an `owl:minCardinality 1` restriction; `key` and `unique (...)` add `owl:hasKey`. Labels, definitions, synonyms and notes map to SKOS with their language tags, on the concept or on the property; `same_as` maps to `owl:equivalentClass`.

**SHACL:** one `sh:NodeShape` per concept targeting its class, with `sh:datatype` or `sh:class`, `sh:minCount`/`sh:maxCount` from `required`, `key` and `many`, `sh:in` for enums, and `sh:minInclusive`, `sh:maxInclusive`, `sh:minLength`, `sh:maxLength`, `sh:pattern` from the rules (formats `email`, `uri`, `uuid` become patterns).

**LinkML:** each concept is a class with `attributes`; `key` maps to `identifier`, `many` to `multivalued`, `unique` and `unique (...)` to `unique_keys`, enums to LinkML enums, rules to `minimum_value`, `maximum_value` and `pattern`, `same_as` to `exact_mappings`, synonyms to `aliases` (`structured_aliases` with a language). The first `is_a` is `is_a`; further parents become `mixins`. Labels and definitions in other languages go to `annotations`.

# PlainOntology file format, version 0.1

A PlainOntology file is UTF-8 plain text, one statement per line. Files conventionally end in `.onto`.

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
  # an indented comment stays inside the concept
  definition "A title the library holds."
```

* Blank lines are ignored.
* `prefix` and `source` lines must come before the first concept.
* `prefix : <IRI>` (empty prefix) sets the base IRI used by exports.
* These prefixes are known without declaring them: `owl rdf rdfs xsd skos schema dcterms foaf prov`.
* A statement line is indented (spaces or tabs) and belongs to the concept above it.

## Concepts

```text
concept NAME [TAG]
```

`NAME` starts with a letter or `_` and may contain letters, digits, `_ . : / -`. Each name appears once per file.

## Statements

| Statement | Meaning | Layer |
|---|---|---|
| `has NAME : TYPE [FLAGS] ["description"]` | an attribute with a datatype | structure |
| `NAME -> CONCEPT [FLAGS] ["description"]` | a relation to another concept (or a `prefix:Term`) | structure |
| `is_a CONCEPT` | subclass of | structure |
| `label "text"` | preferred human name | meaning |
| `definition "text"` | what the concept is | meaning |
| `synonym "text"` | another name people use | meaning |
| `same_as prefix:Term` | the same concept in another vocabulary | meaning |
| `note "text"` | anything else worth knowing | meaning |
| `# text` | a comment | |

**Types:** `text`, `integer`, `decimal`, `boolean`, `date`, `datetime`, `binary`, `json`, `any`, or `enum(a|b|c)`. Enum values may contain spaces but not `|` or `)`.

**Flags:** `key` (identifier), `required`, `unique`, `many` (list or to-many relation).

**Strings** are double-quoted; inside them use `\"` for a quote, `\\` for a backslash and `\n` for a new line.

## Tags

A tag at the end of a concept or statement line says who owns it:

| Tag | Owner | On refresh |
|---|---|---|
| `[auto]` or `[auto: detail]` | the sources | replaced by what the sources say now |
| `[proposed]` or `[proposed: detail]` | an agent, waiting for review | kept; accept by deleting the tag |
| no tag | a person | always kept |

The detail after `auto:` records where a line came from, for example `fk books.author_id`.

## Identity of a line

Merge matches lines by key, one line per key per concept:

| Statement | Key |
|---|---|
| `has` and `->` | the property name (they share one namespace) |
| `label`, `definition` | the statement kind (one each per concept) |
| `is_a`, `same_as` | the target |
| `synonym`, `note`, comment | the text |

## Refresh rules

When `plainonto refresh` re-extracts the sources:

1. Lines without a tag and `[proposed]` lines are kept, in place.
2. Each `[auto]` line is replaced by the extracted line with the same key, or removed if the sources no longer produce it. New extracted lines are appended to their concept.
3. If a person's line has the same key as an extracted line, the person's line wins and the extracted line is not written. The drift report shows both (`=`).
4. A concept that is gone from the sources is removed if it was extracted and holds none of your lines. If it holds your lines, it is kept, its tag is removed, and the report says so (`!`).
5. Refresh is idempotent: running it twice changes nothing the second time.

## Drift report symbols

| Symbol | Meaning |
|---|---|
| `+` | added from the sources |
| `-` | gone from the sources |
| `~` | changed in the sources |
| `=` | your line kept over a different extracted line (not counted as drift) |
| `!` | concept gone from the sources, kept because it holds your lines |

## Exports

**Turtle:** each concept is an `owl:Class`; `has` lines become `owl:DatatypeProperty` and relations `owl:ObjectProperty`, scoped as `Concept/property`. Single-valued properties are also `owl:FunctionalProperty`; `required` and `key` add an `owl:minCardinality 1` restriction. Labels, definitions, synonyms and notes map to SKOS; `same_as` maps to `owl:equivalentClass`.

**LinkML:** each concept is a class with `attributes`; `key` maps to `identifier`, `many` to `multivalued`, enums to LinkML enums, `same_as` to `exact_mappings`, synonyms to `aliases`.

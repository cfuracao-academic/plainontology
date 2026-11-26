"""SHACL export (validated with a real SHACL engine), LinkML extras and language tags."""

import pyshacl
import rdflib
import yaml
from rdflib.namespace import OWL, SKOS

from plainontology.export import to_linkml, to_shacl, to_turtle
from plainontology.textformat import loads

BASE = "https://example.org/lib#"
ONTO = loads(f'''\
prefix : <{BASE}>

concept Work
  label "Work"
  label "Œuvre"@fr

concept Lendable

concept Book
  is_a Work
  is_a Lendable
  synonym "Livre"@fr
  has isbn : text required unique
    label "ISBN"
    length 13..13
  has pages : integer
    min 1
  has status : enum(open|lost)
  has email : text
    format email
  author -> Person required
  unique (pages, status)

concept Person
  has name : text required
''')


def test_shacl_shapes_validate_data():
    shapes = rdflib.Graph().parse(data=to_shacl(ONTO), format="turtle")
    good = f"""
    @prefix : <{BASE}> . @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
    :b1 a :Book ; <{BASE}Book/isbn> "9780441013593" ; <{BASE}Book/pages> 412 ;
        <{BASE}Book/status> "open" ; <{BASE}Book/email> "a@example.org" ; <{BASE}Book/author> :p1 .
    :p1 a :Person ; <{BASE}Person/name> "Frank" .
    """
    conforms, _, _ = pyshacl.validate(rdflib.Graph().parse(data=good, format="turtle"), shacl_graph=shapes)
    assert conforms
    bad = f"""
    @prefix : <{BASE}> .
    :b2 a :Book ; <{BASE}Book/isbn> "123" ; <{BASE}Book/pages> 0 ; <{BASE}Book/status> "stolen" ;
        <{BASE}Book/email> "nope" .
    """
    conforms, _, text = pyshacl.validate(rdflib.Graph().parse(data=bad, format="turtle"), shacl_graph=shapes)
    assert not conforms
    for problem in ("MinLengthConstraint", "MinInclusiveConstraint", "InConstraint", "PatternConstraint", "MinCountConstraint"):
        assert problem in text


def test_turtle_language_tags_and_keys():
    g = rdflib.Graph().parse(data=to_turtle(ONTO), format="turtle")
    work, book = rdflib.URIRef(BASE + "Work"), rdflib.URIRef(BASE + "Book")
    assert (work, SKOS.prefLabel, rdflib.Literal("Œuvre", lang="fr")) in g
    assert (book, SKOS.altLabel, rdflib.Literal("Livre", lang="fr")) in g
    assert (rdflib.URIRef(BASE + "Book/isbn"), SKOS.prefLabel, rdflib.Literal("ISBN")) in g
    assert len(list(g.objects(book, OWL.hasKey))) == 1  # unique (pages, status)


def test_linkml_mixins_unique_keys_and_rules():
    doc = yaml.safe_load(to_linkml(ONTO, "lib"))
    book = doc["classes"]["Book"]
    assert book["is_a"] == "Work" and book["mixins"] == ["Lendable"]
    assert doc["classes"]["Lendable"]["mixin"] is True
    assert set(book["unique_keys"]) == {"isbn_key", "pages_status_key"}
    assert book["attributes"]["pages"]["minimum_value"] == 1
    assert book["attributes"]["isbn"]["pattern"] == "^.{13,13}$" and book["attributes"]["isbn"]["title"] == "ISBN"
    assert book["structured_aliases"] == [{"literal_form": "Livre", "in_language": "fr"}]
    assert doc["classes"]["Work"]["annotations"] == {"label_fr": "Œuvre"}

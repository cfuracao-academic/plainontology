import rdflib
import yaml
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from plainontology.export import to_linkml, to_turtle
from plainontology.textformat import load

from .conftest import EXAMPLES

BASE = "https://example.org/library#"


def test_turtle_parses_and_carries_both_layers():
    g = rdflib.Graph().parse(data=to_turtle(load(EXAMPLES / "library.onto")), format="turtle")
    book = rdflib.URIRef(BASE + "Book")
    author = rdflib.URIRef(BASE + "Author")
    assert (book, RDF.type, OWL.Class) in g
    assert (author, SKOS.prefLabel, rdflib.Literal("Writer")) in g
    assert (book, SKOS.altLabel, rdflib.Literal("Title")) in g
    assert (author, OWL.equivalentClass, rdflib.URIRef("https://schema.org/Person")) in g
    rel = rdflib.URIRef(BASE + "Book/author")
    assert (rel, RDF.type, OWL.ObjectProperty) in g
    assert (rel, RDFS.range, author) in g
    tags = rdflib.URIRef(BASE + "Book/tags")
    assert (tags, RDF.type, OWL.FunctionalProperty) not in g  # many
    assert (rdflib.URIRef(BASE + "SearchCatalogue"), RDFS.subClassOf, rdflib.URIRef(BASE + "Tool")) in g


def test_linkml_is_valid_yaml_with_expected_structure():
    doc = yaml.safe_load(to_linkml(load(EXAMPLES / "library.onto"), "library"))
    assert doc["name"] == "library"
    assert doc["default_prefix"] == "library"
    book = doc["classes"]["Book"]
    assert book["attributes"]["id"] == {"range": "integer", "identifier": True}
    assert book["attributes"]["author"] == {"range": "Author", "required": True}
    assert book["attributes"]["tags"] == {"range": "Tag", "multivalued": True}
    assert book["exact_mappings"] == ["schema:Book"]
    assert doc["prefixes"]["schema"] == "https://schema.org/"
    assert doc["classes"]["ReserveBook"]["is_a"] == "Tool"
    enum = doc["classes"]["ReserveBook"]["attributes"]["pickup"]["range"]
    assert set(doc["enums"][enum]["permissible_values"]) == {"desk", "locker"}

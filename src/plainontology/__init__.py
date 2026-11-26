"""PlainOntology: write down the ontology and semantic layer a system already has,
as one plain text file that people can edit and agents can read."""

__version__ = "0.1.0"

from .check import check, validate_record
from .merge import DriftReport, merge
from .model import Concept, Ontology, Statement, Tag
from .textformat import dumps, load, loads, save

__all__ = [
    "Concept",
    "DriftReport",
    "Ontology",
    "Statement",
    "Tag",
    "__version__",
    "check",
    "dumps",
    "load",
    "loads",
    "merge",
    "save",
    "validate_record",
]

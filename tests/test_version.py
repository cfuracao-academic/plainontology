"""The version is written in three places; a release must keep them equal."""

import re
import sys
from pathlib import Path

import plainontology

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib.loads(text)["project"]["version"]
    return re.search(r'^version = "([^"]+)"', text, re.M)[1]


def test_versions_agree():
    citation = re.search(r"^version: (\S+)", (ROOT / "CITATION.cff").read_text(encoding="utf-8"), re.M)[1]
    assert _pyproject_version() == plainontology.__version__ == citation

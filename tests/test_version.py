"""The version is written in several places; a release must keep them equal."""

import json
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


def test_mcp_registry_entry_matches():
    """server.json (MCP Registry) carries the package version, and the README its ownership line."""
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert server["version"] == _pyproject_version()
    assert all(p["version"] == _pyproject_version() for p in server["packages"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {server['name']} " in readme

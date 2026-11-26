"""Two sources defining one concept are reported; renames can be switched off."""

import json

from plainontology.cli import main
from plainontology.extract import extract_all
from plainontology.merge import merge
from plainontology.textformat import load, loads

from .test_renames import CURRENT


def _schema(path, title, props):
    path.write_text(json.dumps({"title": title, "type": "object", "properties": props}), encoding="utf-8")


def test_same_concept_from_two_sources_is_warned(tmp_path, capsys, monkeypatch):
    _schema(tmp_path / "a.json", "Book", {"title": {"type": "string"}, "pages": {"type": "integer"}})
    _schema(tmp_path / "b.json", "Book", {"title": {"type": "integer"}, "isbn": {"type": "string"}})
    onto = extract_all(["jsonschema:a.json", "jsonschema:b.json"], tmp_path)
    assert [s.body() for s in onto.concepts["Book"].properties()] == [
        "has title : text",
        "has pages : integer",
        "has isbn : text",
    ]  # first source wins on title
    ((name, message),) = onto.warnings
    assert name == "Book" and "jsonschema:a.json and jsonschema:b.json" in message and "1 line(s) differ" in message

    monkeypatch.chdir(tmp_path)
    assert main(["init", "x.onto", "--from", "jsonschema:a.json", "--from", "jsonschema:b.json"]) == 0
    assert "? warning: concept Book is defined by both" in capsys.readouterr().out
    assert main(["drift", "x.onto"]) == 0  # a warning is not drift
    assert "No drift; 1 warning(s)." in capsys.readouterr().out


def test_tool_parent_shared_by_tools_files_is_not_warned(tmp_path):
    for name in ("one", "two"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps([{"name": f"do_{name}", "input_schema": {"type": "object"}}]), encoding="utf-8"
        )
    assert extract_all(["tools:one.json", "tools:two.json"], tmp_path).warnings == []


def test_no_renames_reports_gone_and_added():
    fresh = loads(
        CURRENT.replace("concept Book [auto: sqlite table books]", "concept Volume [auto: sqlite table volumes]")
        .replace('    label "Title"@en\n', "")
        .replace('  definition "A title the library holds."\n', "")
        .replace("book -> Book", "volume -> Volume")
    )
    merged, report = merge(loads(CURRENT), fresh, renames=False)
    assert list(merged.concepts) == ["Book", "Loan", "Volume"]
    assert merged.concepts["Book"].tag is None  # kept because it holds your lines
    kinds = {c.kind for c in report.changes}
    assert "concept_kept" in kinds and "concept_added" in kinds and "concept_renamed" not in kinds


def test_no_renames_flag(tmp_path, capsys):
    db = tmp_path / "lib.sql"
    db.write_text("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, isbn TEXT);\n", encoding="utf-8")
    f = tmp_path / "lib.onto"
    assert main(["init", str(f), "--from", f"sqlite:{db.as_posix()}"]) == 0
    text = f.read_text(encoding="utf-8").replace(
        "has title : text [auto]\n", 'has title : text [auto]\n    label "Title"\n'
    )
    f.write_text(text, encoding="utf-8")
    db.write_text("CREATE TABLE books (id INTEGER PRIMARY KEY, headline TEXT, isbn TEXT);\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["drift", str(f), "--no-renames"]) == 1
    out = capsys.readouterr().out
    assert "renamed" not in out and "is gone from the sources; kept" in out
    assert main(["refresh", str(f)]) == 0
    assert "title renamed to headline" in capsys.readouterr().out
    assert load(f).concepts["Book"].prop("headline").children[0].value == "Title"

import json

from plainontology.cli import main
from plainontology.textformat import load


def test_init_refresh_drift_cycle(examples, monkeypatch, capsys):
    monkeypatch.chdir(examples)
    assert main(["init", "out/lib.onto", "--from", "sqlite:library.sql"]) == 0
    onto = load(examples / "out" / "lib.onto")
    assert onto.sources == ["sqlite:../library.sql"]  # stored relative to the file
    assert main(["init", "out/lib.onto", "--from", "sqlite:library.sql"]) == 2  # no silent overwrite
    assert main(["drift", "out/lib.onto"]) == 0

    # The database changes: a new column appears.
    sql = examples / "library.sql"
    sql.write_text(
        sql.read_text(encoding="utf-8").replace("country TEXT", "country TEXT,\n    website TEXT"), encoding="utf-8"
    )
    capsys.readouterr()
    assert main(["drift", "out/lib.onto"]) == 1
    assert "+ Author: has website : text" in capsys.readouterr().out
    assert main(["refresh", "out/lib.onto"]) == 0
    assert main(["drift", "out/lib.onto"]) == 0


def test_export_and_validate(examples, monkeypatch, capsys):
    monkeypatch.chdir(examples)
    assert main(["export", "library.onto", "--to", "turtle", "-o", "library.ttl"]) == 0
    assert (examples / "library.ttl").read_text(encoding="utf-8").startswith("@prefix")
    (examples / "rec.json").write_text(json.dumps([{"id": 1, "name": "A"}, {"name": 3}]), encoding="utf-8")
    assert main(["validate", "library.onto", "Author", "rec.json"]) == 1
    out = capsys.readouterr().out
    assert "record 0: ok" in out and "record 1: 2 problem(s)" in out and "id: required but missing" in out


def test_check_exit_codes(tmp_path, capsys):
    f = tmp_path / "a.onto"
    f.write_text("concept A\n  b -> Missing\n", encoding="utf-8")
    assert main(["check", str(f)]) == 0
    assert main(["check", str(f), "--strict"]) == 1
    f.write_text("concept A\n  nonsense here\n", encoding="utf-8")
    assert main(["check", str(f)]) == 2
    assert "line 2" in capsys.readouterr().err

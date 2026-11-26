"""Whatever PlainOntology writes, it must read back, and reading then writing
must not change anything. Checked on every example and on awkward inputs."""

import json
import random
import shutil

import pytest

from plainontology.api import init, refresh
from plainontology.model import enum_values, make_enum, quote, unquote
from plainontology.textformat import dumps, loads

from .conftest import EXAMPLES

EXAMPLE_SOURCES = [
    "sqlite:library.sql",
    "postgres:library.postgres.sql",
    "mysql:library.mysql.sql",
    "jsonschema:article.schema.json",
    "openapi:library.openapi.yaml",
    "dbt:dbt/manifest.json",
    "tools:agent-tools.json",
]

AWKWARD = "a\"b\\c\nd\re\tf\u2028g\u2029h\x85i\x0bj\x0ck\x1cl|m(n)o[p]q é 日本 ' `"


def _stable(text: str) -> None:
    once = dumps(loads(text))
    assert dumps(loads(once)) == once


@pytest.mark.parametrize("source", EXAMPLE_SOURCES)
def test_every_example_reads_back_and_refresh_is_stable(examples, source):
    path = examples / "out.onto"
    init(path, [f"{source}"]) if False else None
    _onto, _ = init(path, [source.split(":", 1)[0] + ":" + str(examples / source.split(":", 1)[1])])
    text = path.read_text(encoding="utf-8")
    _stable(text)
    report = refresh(path)
    assert not report.has_drift
    assert path.read_text(encoding="utf-8") == text


def test_quote_round_trips_random_strings():
    rng = random.Random(20261126)
    alphabet = AWKWARD + "abcXYZ019 _-.:/@#[]{}<>!?"
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        quoted = quote(text)
        assert "\n" not in quoted and "\r" not in quoted and "\u2028" not in quoted
        assert len(quoted.splitlines()) == 1
        assert unquote(quoted[1:-1]) == text


def test_enum_values_round_trip_random():
    rng = random.Random(7)
    alphabet = AWKWARD + "abc "
    for _ in range(300):
        values = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8))) for _ in range(rng.randint(1, 4))]
        values = [v for v in dict.fromkeys(values) if v.strip()]
        if not values:
            continue
        datatype = make_enum(values)
        assert enum_values(datatype) == values
        line = f"concept A\n  has x : {datatype}\n"
        assert enum_values(loads(line).concepts["A"].prop("x").target) == values


def test_texts_with_any_character_survive_a_file(tmp_path):
    rng = random.Random(42)
    alphabet = AWKWARD + "abc"
    for _ in range(100):
        value = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 30)))
        text = f"concept A\n  definition {quote(value)}\n  note {quote(value)}@fr\n"
        onto = loads(text)
        assert onto.concepts["A"].definition() == value
        _stable(text)


def test_awkward_json_schema_reads_back(tmp_path):
    schema = {
        "title": "Odd",
        "description": 'Line one\u2028line two "quoted" back\\slash',
        "type": "object",
        "properties": {
            "élan": {"type": "string", "description": "accented"},
            "-x": {"type": "integer"},
            "9lives": {"type": "number", "minimum": 0.00001, "maximum": 1e20},
            "日本": {"type": "string"},
            "kind": {"enum": ["a\nb", "S", "M (medium)", "L|XL", ' "q" ']},
            "code": {"type": "string", "pattern": "^\\d{4}\\\\x$"},
            "item": {"$ref": "#/$defs/Item[v2]"},
        },
        "$defs": {"Item[v2]": {"type": "object", "properties": {"n": {"type": "integer"}}}},
    }
    (tmp_path / "odd.json").write_text(json.dumps(schema), encoding="utf-8")
    onto, _ = init(tmp_path / "odd.onto", [f"jsonschema:{(tmp_path / 'odd.json').as_posix()}"])
    _stable((tmp_path / "odd.onto").read_text(encoding="utf-8"))
    odd = onto.concepts["Odd"]
    assert odd.definition() == 'Line one line two "quoted" back\\slash'  # descriptions use single spaces
    assert enum_values(odd.prop("kind").target) == ["a\nb", "S", "M (medium)", "L|XL", ' "q" ']
    assert odd.prop("_9lives").child("min").value == "0.00001"
    assert odd.prop("_9lives").child("max").value == "100000000000000000000"
    assert odd.prop("code").child("pattern").value == "^\\d{4}\\\\x$"
    assert odd.prop("élan") is not None and odd.prop("日本") is not None and odd.prop("_-x") is not None


def test_awkward_sql_reads_back(tmp_path):
    ddl = (
        "\ufeffCREATE TABLE public.readings (\n"
        "    id integer PRIMARY KEY,\n"
        "    code text CHECK (code ~ '\\ycode\\y'),\n"
        "    other text CHECK (other ~ '[[:alpha:]+'),\n"
        "    word text CHECK (word ~ '^[[:alpha:]]+$'),\n"
        "    ratio numeric CHECK (ratio >= 0.00001),\n"
        "    n integer CHECK (n > 2.5)\n"
        ");\n"
        "COMMENT ON TABLE public.readings IS 'Line one\u2028line two';\n"
        "CREATE TABLE public.book (id integer);\n"
        "CREATE TABLE public.books (id integer);\n"
        'CREATE TABLE public."日本" (id integer);\n'
        'CREATE TABLE public."中国" (id integer);\n'
    )
    (tmp_path / "odd.sql").write_text(ddl, encoding="utf-8")
    onto, report = init(tmp_path / "odd.onto", [f"postgres:{(tmp_path / 'odd.sql').as_posix()}"])
    _stable((tmp_path / "odd.onto").read_text(encoding="utf-8"))
    readings = onto.concepts["Reading"]
    assert readings.definition() == "Line one\u2028line two"
    assert readings.prop("code").child("pattern").value == "\\bcode\\b"  # PostgreSQL \y is \b in Python
    assert readings.prop("other").child("pattern") is None  # not a usable regex: kept as a note instead
    assert any("not usable outside the database" in s.value for s in readings.all("note"))
    assert readings.prop("word").child("pattern").value == "^[a-zA-Z\u00c0-\u024f]+$"  # POSIX class translated
    assert readings.prop("ratio").child("min").value == "0.00001"
    assert readings.prop("n").child("min").value == "3"  # n > 2.5 on an integer
    assert {"Book", "Books", "日本", "中国"} <= set(onto.concepts)
    assert any("would both be concept Book" in c.line for c in report.warnings)


def test_people_lines_keep_their_exact_text_through_refresh(examples):
    path = examples / "lib.onto"
    init(path, [f"sqlite:{(examples / 'library.sql').as_posix()}"])
    text = path.read_text(encoding="utf-8")
    mine = '\n  note   "C:\\docs\\new and a regex ^\\d+$"\n  label "Livre"@fr\n\n  # my comment, after a blank line\n'
    text = text.replace("  has title : text required [auto]\n", "  has title : text required [auto]\n" + mine, 1)
    path.write_text(text, encoding="utf-8")
    sql = examples / "library.sql"
    sql.write_text(
        sql.read_text(encoding="utf-8").replace("country TEXT", "country TEXT, website TEXT"), encoding="utf-8"
    )
    report = refresh(path)
    assert report.has_drift
    after = path.read_text(encoding="utf-8")
    assert mine in after  # byte for byte, spacing and escapes included
    assert "has website : text [auto]" in after


def test_examples_folder_is_untouched(examples):
    shutil.rmtree(examples / "dbt")  # the fixture is a private copy
    assert (EXAMPLES / "dbt" / "manifest.json").exists()

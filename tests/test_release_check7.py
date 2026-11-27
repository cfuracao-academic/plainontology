"""Findings of the diff-only check of the MySQL statement splitter (tests written before the fixes)."""

import time

import pytest

from plainontology.extract import extract_all

pytest.importorskip("sqlglot")


def _read(tmp_path, sql):
    (tmp_path / "r.sql").write_text(sql, encoding="utf-8")
    onto = extract_all(["mysql:r.sql"], tmp_path)
    return onto, [m for _c, m in onto.warnings]


def test_begin_and_end_as_column_names_in_a_trigger(tmp_path):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE shift (id int primary key, begin datetime, end datetime);\n"
        "CREATE TRIGGER shift_bu BEFORE UPDATE ON shift FOR EACH ROW SET NEW.begin = COALESCE(NEW.begin, OLD.begin);\n"
        "CREATE TABLE booking (id int primary key, shift_id int, FOREIGN KEY (shift_id) REFERENCES shift (id));\n",
    )
    assert sorted(onto.concepts) == ["Booking", "Shift"] and warnings == []


def test_unclosed_block_keeps_the_rest_and_stays_fast(tmp_path):
    tables = "".join(f"CREATE TABLE t{i} (id int primary key);\n" for i in range(2000))
    start = time.monotonic()
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE a (id int primary key);\nCREATE PROCEDURE p() BEGIN SELECT 1;\n" + tables,  # never closed
    )
    assert time.monotonic() - start < 20
    assert len(onto.concepts) == 2001
    assert any("routine" in w for w in warnings)


@pytest.mark.parametrize(
    "middle",
    [
        "CREATE TABLE csv_import (\n  id int primary key,\n  delimiter CHAR(1) NOT NULL DEFAULT ','\n);\n",
        "UPDATE settings SET\n  delimiter = ';';\n",
        "CREATE TABLE notes (\n  id int primary key,\n"
        "  sep varchar(5) COMMENT 'used as the field\ndelimiter in exports'\n);\n",
    ],
)
def test_lines_starting_with_the_word_delimiter(tmp_path, middle):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE settings (id int primary key, delimiter varchar(5));\n"
        + middle
        + "CREATE TABLE t2 (id int primary key, n int CHECK (n > 0));\n",
    )
    assert "T2" in onto.concepts and onto.concepts["T2"].prop("n").child("min") is not None
    assert warnings == []
    if "csv_import" in middle:
        assert onto.concepts["CsvImport"].prop("delimiter") is not None
    if "notes" in middle:
        assert onto.concepts["Note"].prop("sep") is not None


def test_commented_out_triggers_make_no_notes(tmp_path):
    onto, _warnings = _read(
        tmp_path,
        "CREATE TABLE a (id int primary key);\nCREATE TABLE b (id int primary key);\n"
        "-- CREATE TRIGGER a_bi BEFORE INSERT ON a FOR EACH ROW SET @x = 1;\n"
        "/* CREATE TRIGGER b_bu BEFORE UPDATE ON b FOR EACH ROW SET @y = 1; */\n"
        "CREATE /* c */ TRIGGER a_ai AFTER INSERT ON a FOR EACH ROW SET @z = 1;\n",
    )
    notes = [s.value for c in onto.concepts.values() for s in c.all("note")]
    assert notes == ["trigger a_ai: AFTER INSERT"]

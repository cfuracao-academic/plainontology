"""MySQL stored routines are set aside by a statement splitter that knows strings,
comments and DELIMITER blocks (tests written before the change)."""

import pytest

from plainontology.extract import extract_all

pytest.importorskip("sqlglot")


def _read(tmp_path, sql):
    (tmp_path / "r.sql").write_text(sql, encoding="utf-8")
    onto = extract_all(["mysql:r.sql"], tmp_path)
    return onto, [m for _c, m in onto.warnings]


def test_comment_before_a_procedure_in_a_delimiter_block(tmp_path):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE a (id int primary key);\n\nDELIMITER $$\n-- Lists a\nCREATE PROCEDURE list_a()\nBEGIN\n"
        "  SELECT * FROM a;\nEND$$\nDELIMITER ;\n\n"
        "CREATE TABLE b (id int primary key, a_id int, FOREIGN KEY (a_id) REFERENCES a (id));\n"
        "CREATE TABLE c (id int primary key);\nALTER TABLE a ADD COLUMN note varchar(20);\n"
        "CREATE TABLE d (id int primary key, c_id int, FOREIGN KEY (c_id) REFERENCES c (id));\n"
        "CREATE VIEW v AS SELECT id FROM d;\n",
    )
    assert sorted(onto.concepts) == ["A", "B", "C", "D", "V"] and onto.concepts["A"].prop("note") is not None
    assert warnings == []


def test_two_procedures_with_a_header_comment(tmp_path):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE a (id int primary key);\n\nDELIMITER //\n\nCREATE PROCEDURE list_a()\nBEGIN\n"
        "  SELECT * FROM a;\nEND //\n\n-- Remove everything\nCREATE PROCEDURE clear_a()\nBEGIN\n"
        "  DELETE FROM a;\nEND //\n\nDELIMITER ;\n\n"
        "CREATE TABLE b (id int primary key, a_id int, FOREIGN KEY (a_id) REFERENCES a (id));\n"
        "CREATE TABLE c (id int primary key);\n",
    )
    assert sorted(onto.concepts) == ["A", "B", "C"] and warnings == []


@pytest.mark.parametrize("after", ["  -- default total", " ", "", " # note"])
def test_single_statement_trigger_followed_by_a_comment_or_space(tmp_path, after):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE orders (id int primary key, total decimal(10,2));\n"
        f"CREATE TRIGGER orders_bi BEFORE INSERT ON orders FOR EACH ROW SET NEW.total = IFNULL(NEW.total, 0);{after}\n"
        "CREATE TABLE order_line (id int primary key, order_id int, FOREIGN KEY (order_id) REFERENCES orders (id));\n"
        "CREATE TABLE refund (id int primary key, order_id int);\n",
    )
    assert sorted(onto.concepts) == ["Order", "OrderLine", "Refund"] and warnings == []
    assert any("orders_bi" in s.value for s in onto.concepts["Order"].all("note"))


def test_triggers_with_if_and_begin_blocks_without_delimiter_keep_file_order(tmp_path):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE a (id int primary key, x int);\n"
        "CREATE TRIGGER a_bi BEFORE INSERT ON a FOR EACH ROW\nIF NEW.x IS NULL THEN\n  SET NEW.x = 1;\nEND IF;\n"
        "CREATE TABLE log (id int primary key, msg varchar(10));\n"
        "CREATE TRIGGER a_ai AFTER INSERT ON a FOR EACH ROW\nBEGIN\n"
        "  INSERT INTO log (id, msg) VALUES (NEW.id, CASE WHEN NEW.x > 1 THEN 'big' ELSE 'x' END);\nEND;\n"
        "CREATE TABLE b (id int primary key, a_id int, FOREIGN KEY (a_id) REFERENCES a (id));\n",
    )
    assert sorted(onto.concepts) == ["A", "B", "Log"] and warnings == []
    notes = [s.value for s in onto.concepts["A"].all("note")]
    assert [n.split(":")[1].strip() for n in notes if n.startswith("trigger")] == [
        ["a_bi", " BEFORE INSERT"][0],
        ["a_ai", " AFTER INSERT"][0],
    ] or notes.index(next(n for n in notes if "a_bi" in n)) < notes.index(next(n for n in notes if "a_ai" in n))


def test_delimiter_inside_a_comment_or_string_in_a_routine_body(tmp_path):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE a (id int primary key);\nDELIMITER //\nCREATE PROCEDURE p()\nBEGIN\n"
        "  -- see https://x.org/a//\n  SELECT 'a//b';\nEND //\nDELIMITER ;\n"
        "CREATE TABLE b (id int primary key);\n",
    )
    assert sorted(onto.concepts) == ["A", "B"] and warnings == []


def test_statement_open_at_a_delimiter_line_stays_in_place(tmp_path):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE a (id int primary key);\nDELIMITER $$\nCREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\nEND$$ -- p\n"
        "DELIMITER ;\nCREATE TABLE b (id int primary key, a_id int REFERENCES a (id));\n",
    )
    assert sorted(onto.concepts) == ["A", "B"] and warnings == []


def test_if_function_and_if_exists_in_routines_open_no_block(tmp_path):
    onto, warnings = _read(
        tmp_path,
        "CREATE TABLE a (id int primary key, x int);\n"
        "CREATE TRIGGER IF NOT EXISTS a_bi BEFORE INSERT ON a FOR EACH ROW SET NEW.x = IF(NEW.x > 0, NEW.x, 0);\n"
        "CREATE PROCEDURE p() BEGIN DROP TABLE IF EXISTS tmp; SELECT REPEAT('a', 3); END;\n"
        "CREATE TABLE b (id int primary key, a_id int, FOREIGN KEY (a_id) REFERENCES a (id));\n",
    )
    assert sorted(onto.concepts) == ["A", "B"] and warnings == []

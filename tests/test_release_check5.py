"""A table after a MySQL routine written with a custom DELIMITER is not lost (test written before the fix)."""

import pytest

from plainontology.extract import extract_all

pytest.importorskip("sqlglot")

ROUTINE = {
    "$$": "DELIMITER $$\nCREATE PROCEDURE pr()\nBEGIN\n  -- it's here\n  SELECT 1;\nEND$$\nDELIMITER ;\n",
    "//": "DELIMITER //\nCREATE PROCEDURE pr()\nBEGIN\n  /* it's */ SELECT 1; -- x\nEND //\nDELIMITER ;\n",
    "##": "DELIMITER ##\nCREATE TRIGGER tr BEFORE INSERT ON p FOR EACH ROW BEGIN SET NEW.v = 1; END##\nDELIMITER ;\n",
    ";;": "DELIMITER ;;\nCREATE PROCEDURE pr()\nBEGIN\n  SELECT 1;\nEND ;;\nDELIMITER ;\n",
}


@pytest.mark.parametrize("delimiter", list(ROUTINE))
def test_table_after_a_routine_with_a_custom_delimiter(tmp_path, delimiter):
    sql = (
        "CREATE TABLE p (id int NOT NULL PRIMARY KEY, v int) ENGINE=InnoDB;\n"
        + ROUTINE[delimiter]
        + "CREATE TABLE q (id int primary key, p_id int, FOREIGN KEY (p_id) REFERENCES p (id));\n"
    )
    (tmp_path / "d.sql").write_text(sql, encoding="utf-8")
    onto = extract_all(["mysql:d.sql"], tmp_path)
    assert sorted(onto.concepts) == ["P", "Q"]
    assert onto.concepts["Q"].prop("p").target == "P"
    assert [m for _c, m in onto.warnings] == []

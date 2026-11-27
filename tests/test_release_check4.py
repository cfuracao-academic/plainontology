"""Findings of the diff-only check of a396559..84c384b (tests written before the fixes)."""

import json

import pytest

from plainontology.extract import extract_all

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


def _warnings(onto):
    return [m for _c, m in onto.warnings]


READINGS = """CREATE TABLE sensors (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE readings (
  sensor_id INT UNSIGNED NOT NULL,
  taken DATETIME NOT NULL,
  value DECIMAL(10,3) NOT NULL,
  PRIMARY KEY (sensor_id, taken)
) ENGINE=InnoDB
{comment}
PARTITION BY RANGE (YEAR(taken)) (
  PARTITION p2025 VALUES LESS THAN (2026),
  PARTITION pmax VALUES LESS THAN MAXVALUE
);

CREATE TABLE alerts (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  sensor_id INT UNSIGNED NOT NULL,
  message TEXT,
  CONSTRAINT fk_alert_sensor FOREIGN KEY (sensor_id) REFERENCES sensors (id)
) ENGINE=InnoDB;
"""


@pytest.mark.parametrize(
    "comment", ["-- one partition per year, see ops runbook", "# one partition per year", "/* yearly */"]
)
def test_comment_before_partition_by_keeps_every_table(tmp_path, comment):
    _write(tmp_path, "v.sql", READINGS.format(comment=comment))
    onto = extract_all(["mysql:v.sql"], tmp_path)
    assert sorted(onto.concepts) == ["Alert", "Reading", "Sensor"]
    assert onto.concepts["Alert"].prop("sensor").target == "Sensor"
    assert [s.value for s in onto.concepts["Reading"].all("note")] == ["partitioned by RANGE (YEAR(taken))"]
    assert _warnings(onto) == []


def test_comments_inside_partition_maintenance_are_quiet(tmp_path):
    _write(
        tmp_path,
        "m.sql",
        "CREATE TABLE `logs` (`id` int NOT NULL, `d` date NOT NULL, PRIMARY KEY (`id`,`d`)) ENGINE=InnoDB\n"
        "PARTITION BY HASH (id) PARTITIONS 4;\n"
        "ALTER TABLE logs /* old */ DROP PARTITION p3;\n"
        "ALTER TABLE logs -- remove the 2019 data\n  DROP PARTITION p4;\n"
        "ALTER TABLE `logs` -- reorganise\n  REORGANIZE PARTITION pmax INTO (PARTITION p5 VALUES LESS THAN (2026));\n"
        "ALTER TABLE /* t */ logs DROP PARTITION p6;\n"
        "ALTER TABLE logs\n  -- yearly roll\n  ADD PARTITION (PARTITION p7 VALUES LESS THAN (2031));\n"
        "ALTER TABLE logs # note\n  TRUNCATE PARTITION p1;\n"
        "ALTER TABLE logs ADD COLUMN extra int;\n",
    )
    onto = extract_all(["mysql:m.sql"], tmp_path)
    assert onto.concepts["Log"].prop("extra") is not None and _warnings(onto) == []


def test_comment_between_partition_by_and_the_method(tmp_path):
    _write(
        tmp_path,
        "h.sql",
        "CREATE TABLE t (id int NOT NULL PRIMARY KEY) ENGINE=InnoDB PARTITION BY /* spread */ HASH (id) PARTITIONS 4;\n",
    )
    onto = extract_all(["mysql:h.sql"], tmp_path)
    assert [s.value for s in onto.concepts["T"].all("note")] == ["partitioned by HASH (id)"]


def test_union_enum_keeps_first_order_and_one_of_each_number(tmp_path):
    schema = {
        "type": "object",
        "properties": {
            "c": {"anyOf": [{"enum": ["red", "green"]}, {"enum": ["blue", "red"]}]},
            "n": {"anyOf": [{"enum": [1, 2]}, {"enum": [2.0, 3]}]},
        },
    }
    _write(tmp_path, "u.json", json.dumps(schema))
    u = extract_all(["jsonschema:u.json"], tmp_path).concepts["U"]
    assert u.prop("c").target == "enum(red|green|blue)" and u.prop("n").target == "enum(1|2|3)"

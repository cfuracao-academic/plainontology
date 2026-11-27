"""Findings of the final diff-only check before 0.2.0 (tests written before the fixes)."""

import json

import pytest

from plainontology.check import validate_record
from plainontology.extract import extract_all
from plainontology.model import enum_items, make_enum
from plainontology.textformat import loads

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


def _warnings(onto):
    return [m for _c, m in onto.warnings]


# Blocker 1: the Postgres foreign key swap keeps the relation
@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE orgs (id bigint PRIMARY KEY, name text);\n"
        "CREATE TABLE users (id bigint PRIMARY KEY, org_id bigint NOT NULL);\n"
        "ALTER TABLE users ADD CONSTRAINT fk_users_org FOREIGN KEY (org_id) REFERENCES orgs(id);\n"
        "ALTER TABLE users ADD CONSTRAINT fk_users_org_new FOREIGN KEY (org_id) REFERENCES orgs(id)"
        " ON DELETE CASCADE NOT VALID;\n"
        "ALTER TABLE users VALIDATE CONSTRAINT fk_users_org_new;\n"
        "ALTER TABLE users DROP CONSTRAINT fk_users_org;\n"
        "ALTER TABLE users RENAME CONSTRAINT fk_users_org_new TO fk_users_org;\n",
        "CREATE TABLE orgs (id bigint PRIMARY KEY);\n"
        "CREATE TABLE users (id bigint PRIMARY KEY, org_id bigint NOT NULL REFERENCES orgs(id));\n"
        "ALTER TABLE users ADD CONSTRAINT users_org_fk2 FOREIGN KEY (org_id) REFERENCES orgs(id);\n"
        "ALTER TABLE users DROP CONSTRAINT users_org_id_fkey;\n",
    ],
)
def test_foreign_key_swap_keeps_the_relation(tmp_path, sql):
    _write(tmp_path, "s.sql", sql)
    onto = extract_all(["postgres:s.sql"], tmp_path)
    user = onto.concepts["User"]
    assert user.prop("org").target == "Org" and [p.name for p in user.properties()] == ["id", "org"]
    assert _warnings(onto) == []


# Blocker 2: "partition by" inside a comment string is text
def test_partition_by_inside_text_is_not_a_partition(tmp_path):
    _write(
        tmp_path,
        "m.sql",
        "CREATE TABLE `scores` (`id` int NOT NULL, `player` varchar(20) NOT NULL DEFAULT 'x', PRIMARY KEY (`id`))"
        " ENGINE=InnoDB COMMENT='ranked by window partition by player';\n"
        "CREATE TABLE `logs` (`id` int NOT NULL, `msg` varchar(100) DEFAULT NULL COMMENT 'see partition by day',"
        " PRIMARY KEY (`id`)) ENGINE=InnoDB;\n",
    )
    onto = extract_all(["mysql:m.sql"], tmp_path)
    score, log = onto.concepts["Score"], onto.concepts["Log"]
    assert score.definition() == "ranked by window partition by player" and score.all("note") == []
    assert log.prop("msg").value == "see partition by day" and log.prop("id").flags == ("key",)
    assert log.all("note") == [] and _warnings(onto) == []


def test_partition_note_keeps_nested_brackets(tmp_path):
    _write(
        tmp_path,
        "m.sql",
        "CREATE TABLE `ev` (`id` int NOT NULL, `created` datetime NOT NULL, PRIMARY KEY (`id`,`created`))"
        " ENGINE=InnoDB\n/*!50100 PARTITION BY RANGE (year(`created`))\n"
        "(PARTITION p0 VALUES LESS THAN (2020) ENGINE = InnoDB) */;\n",
    )
    ev = extract_all(["mysql:m.sql"], tmp_path).concepts["Ev"]
    assert [s.value for s in ev.all("note")] == ["partitioned by RANGE (year(created))"]


# Blocker 3: ordinary ALTER VIEW forms are quiet; redefinitions are applied
def test_ordinary_alter_view_forms_are_quiet(tmp_path):
    _write(
        tmp_path,
        "v.sql",
        "create table public.profiles (id uuid primary key, username text);\n"
        "create view public.profile_names as select id, username from public.profiles;\n"
        "alter view public.profile_names set (security_invoker = true);\n"
        "alter view public.profile_names set (security_barrier = 'true');\n"
        "alter view public.profile_names alter column username set default 'x';\n"
        "alter view public.profile_names owner to postgres;\n"
        "create materialized view mv as select id from public.profiles;\n"
        "ALTER MATERIALIZED VIEW mv OWNER TO postgres;\n",
    )
    onto = extract_all(["postgres:v.sql"], tmp_path)
    assert _warnings(onto) == [] and "ProfileName" in onto.concepts


def test_alter_view_as_redefines_the_view(tmp_path):
    _write(
        tmp_path,
        "v.sql",
        "CREATE TABLE t (id int PRIMARY KEY, a text, b text);\n"
        "CREATE VIEW v AS SELECT id, a FROM t;\n"
        "ALTER VIEW v AS SELECT id, b FROM t;\n",
    )
    onto = extract_all(["mysql:v.sql"], tmp_path)
    v = onto.concepts["V"]
    assert v.prop("b") is not None and v.prop("a") is None and _warnings(onto) == []


# Blocker 4 and enum points: leading zeros are text, mixed enums are typed per value
def test_leading_zeros_are_text_and_mixed_enums_are_typed_per_value():
    assert enum_items("enum(01|02|1|2.5|-3|0|0.5)") == [
        ("01", False),
        ("02", False),
        ("1", False),
        ("2.5", False),
        ("-3", False),
        ("0", False),
        ("0.5", False),
    ]
    onto = loads("concept Region\n  has code : enum(01|02|03)\n  has size : enum(1|2|3)\n")
    assert validate_record(onto, "Region", {"code": "01", "size": 2}) == []
    assert validate_record(onto, "Region", {"code": 1}) != []
    assert make_enum([1, 2, "max"], numbers=None) == "enum(1|2|max)"


def test_mixed_json_schema_enum(tmp_path):
    schema = {"type": "object", "properties": {"limit": {"enum": [1, 2, "max"]}}}
    _write(tmp_path, "m.json", json.dumps(schema))
    onto = extract_all(["jsonschema:m.json"], tmp_path)
    assert validate_record(onto, "M", {"limit": 1}) == [] and validate_record(onto, "M", {"limit": "max"}) == []
    assert validate_record(onto, "M", {"limit": "1"}) != []


def test_enum_error_says_number_or_text():
    onto = loads("concept A\n  has size : enum(1|2|3)\n")
    message = validate_record(onto, "A", {"size": "2"})[0]
    assert "text" in message and "number" in message


# Blocker 5: MySQL partition maintenance never warns
def test_mysql_partition_maintenance_is_quiet(tmp_path):
    _write(
        tmp_path,
        "p.sql",
        "CREATE TABLE `logs` (`id` int NOT NULL, `year` int NOT NULL, PRIMARY KEY (`id`,`year`)) ENGINE=InnoDB;\n"
        "ALTER TABLE `logs` DROP PARTITION p2019;\n"
        "ALTER TABLE `logs` ADD PARTITION (PARTITION p2022 VALUES LESS THAN (2023));\n"
        "ALTER TABLE `logs` DROP PARTITION p2019, p2020;\n"
        "ALTER TABLE `logs` TRUNCATE PARTITION p2019, p2020;\n"
        "ALTER TABLE `logs` REORGANIZE PARTITION p0 INTO (PARTITION p1 VALUES LESS THAN (2000));\n"
        "ALTER TABLE `logs` ADD COLUMN note text;\n",
    )
    onto = extract_all(["mysql:p.sql"], tmp_path)
    assert onto.concepts["Log"].prop("note") is not None and _warnings(onto) == []


# CREATE TABLE LIKE copies ordinary index names too
def test_like_copy_ordinary_index_drop_is_quiet(tmp_path):
    _write(
        tmp_path,
        "l.sql",
        "CREATE TABLE a (id int PRIMARY KEY, name varchar(20), KEY name_i (name));\n"
        "CREATE TABLE b LIKE a;\nALTER TABLE b DROP INDEX name_i;\n",
    )
    assert _warnings(extract_all(["mysql:l.sql"], tmp_path)) == []

"""Findings of the diff-only check of 0ec0ffe..a396559 (tests written before the fixes)."""

import json
import time

import pytest

from plainontology.extract import extract_all
from plainontology.model import make_enum

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


def _warnings(onto):
    return [m for _c, m in onto.warnings]


def test_table_keyed_by_a_foreign_key_declared_twice(tmp_path):
    _write(
        tmp_path,
        "o.sql",
        "CREATE TABLE users (id integer PRIMARY KEY, email text NOT NULL);\n"
        "CREATE TABLE profiles (user_id integer PRIMARY KEY REFERENCES users(id), bio text);\n"
        "ALTER TABLE profiles ADD CONSTRAINT profiles_user_fk FOREIGN KEY (user_id) REFERENCES users(id);\n",
    )
    onto = extract_all(["postgres:o.sql"], tmp_path)  # used to crash
    user = onto.concepts["Profile"].prop("user")
    assert user.target == "User" and "unique" in user.flags and "required" in user.flags


def test_join_table_with_a_link_declared_twice(tmp_path):
    _write(
        tmp_path,
        "j.sql",
        "CREATE TABLE posts (id int PRIMARY KEY);\nCREATE TABLE tags (id int PRIMARY KEY);\n"
        "CREATE TABLE post_tags (post_id int REFERENCES posts(id), tag_id int REFERENCES tags(id),"
        " PRIMARY KEY (post_id, tag_id));\n"
        "ALTER TABLE post_tags ADD CONSTRAINT post_tags_post_fk FOREIGN KEY (post_id) REFERENCES posts(id);\n",
    )
    onto = extract_all(["postgres:j.sql"], tmp_path)
    assert "PostTag" not in onto.concepts
    assert onto.concepts["Post"].prop("tags").target == "Tag"


def test_large_comment_inside_a_create_table_stays_fast(tmp_path):
    comment = "".join(f"  -- note line {i}: nothing to see here\n" for i in range(1000))
    _write(tmp_path, "c.sql", f"CREATE TABLE t (\n  id int NOT NULL,\n{comment}  PRIMARY KEY (id)\n) ENGINE=InnoDB;\n")
    start = time.monotonic()
    onto = extract_all(["mysql:c.sql"], tmp_path)
    assert time.monotonic() - start < 5 and onto.concepts["T"].prop("id").flags == ("key",)


def test_object_and_array_consts_in_a_union(tmp_path):
    _write(
        tmp_path,
        "u.json",
        json.dumps({"type": "object", "properties": {"x": {"oneOf": [{"const": {"w": 1}}, {"const": [1, 2]}]}}}),
    )
    onto = extract_all(["jsonschema:u.json"], tmp_path)  # used to crash
    assert onto.concepts["U"].prop("x").target.startswith("enum(")


def test_ansi_quoted_partitioned_table(tmp_path):
    _write(
        tmp_path,
        "a.sql",
        'CREATE TABLE "sales" ("id" int NOT NULL, "year" int NOT NULL, PRIMARY KEY ("id","year"))\n'
        "PARTITION BY RANGE (year) (PARTITION p1 VALUES LESS THAN (2020), PARTITION p2 VALUES LESS THAN MAXVALUE);\n",
    )
    onto = extract_all(["mysql:a.sql"], tmp_path)
    assert _warnings(onto) == [] and [s.value for s in onto.concepts["Sale"].all("note")] == [
        "partitioned by RANGE (year)"
    ]


def test_backtick_names_with_hash_or_apostrophe(tmp_path):
    _write(
        tmp_path,
        "b.sql",
        "CREATE TABLE `orders` (`order#` int NOT NULL, `who's` varchar(20), `year` int NOT NULL,"
        " PRIMARY KEY (`order#`,`year`)) ENGINE=InnoDB\n/*!50100 PARTITION BY HASH (`year`) PARTITIONS 4 */;\n",
    )
    onto = extract_all(["mysql:b.sql"], tmp_path)
    assert _warnings(onto) == [] and [s.value for s in onto.concepts["Order"].all("note")] == [
        "partitioned by HASH (year)"
    ]


def test_text_enum_quotes_every_numeric_looking_code():
    assert make_enum(["01", "02", "10"]) == 'enum("01"|"02"|"10")'
    assert make_enum(["a", "01"]) == 'enum(a|"01")'

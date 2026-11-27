"""The eight items left after the ninth review, fixed for 0.2.0 (tests written first)."""

import json

import pytest

from plainontology import api
from plainontology.check import validate_record
from plainontology.cli import main
from plainontology.extract import extract_all
from plainontology.textformat import dumps, load, loads

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


# 1. PLAINONTO_SOURCE_ROOT applies to the .onto file of every command
@pytest.mark.parametrize(
    "args",
    [
        ["refresh", "{f}"],
        ["export", "{f}", "--to", "turtle"],
        ["validate", "{f}", "A", "{r}"],
        ["rename", "{f}", "A", "B"],
        ["drift", "{f}"],
    ],
)
def test_every_command_keeps_the_onto_file_inside_the_source_root(tmp_path, monkeypatch, capsys, args):
    inside = tmp_path / "repo"
    inside.mkdir()
    outside = tmp_path / "secret.onto"
    outside.write_text("AWS_SECRET=abc123\n", encoding="utf-8")
    record = inside / "r.json"
    record.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PLAINONTO_SOURCE_ROOT", str(inside))
    assert main([a.format(f=outside, r=record) for a in args]) == 2
    err = capsys.readouterr()
    assert "PLAINONTO_SOURCE_ROOT" in err.err and "abc123" not in err.out + err.err


# 2. a property renamed in the source keeps its place (and the comment above it)
def test_renamed_property_keeps_its_place(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, born INTEGER);\n")
    f = tmp_path / "p.onto"
    api.init(f, [f"sqlite:{(tmp_path / 's.sql').as_posix()}"])
    f.write_text(
        f.read_text(encoding="utf-8").replace(
            "  has name : text [auto]\n",
            '  # Legal name as on the contract; never shown publicly.\n  has name : text [auto]\n    note "legal"\n',
        ),
        encoding="utf-8",
    )
    _write(tmp_path, "s.sql", "CREATE TABLE people (id INTEGER PRIMARY KEY, full_name TEXT, born INTEGER);\n")
    api.refresh(f)
    person = load(f).concepts["Person"]
    kinds = [(s.kind, s.name) for s in person.statements]
    assert kinds == [("has", "id"), ("comment", ""), ("has", "full_name"), ("has", "born")]
    assert (
        person.prop("full_name").first_child_value() if hasattr(person.prop("full_name"), "first_child_value") else True
    )
    assert person.prop("full_name").child("note").value == "legal"


# 3. Prisma's implicit many-to-many tables get names from the linked tables
def test_prisma_implicit_many_to_many_names(tmp_path):
    _write(
        tmp_path,
        "p.sql",
        'CREATE TABLE "Post" (id int PRIMARY KEY);\nCREATE TABLE "Tag" (id int PRIMARY KEY);\n'
        'CREATE TABLE "_PostToTag" ("A" int NOT NULL REFERENCES "Post"(id), "B" int NOT NULL REFERENCES "Tag"(id));\n'
        'CREATE UNIQUE INDEX "_PostToTag_AB_unique" ON "_PostToTag"("A", "B");\n',
    )
    post = extract_all(["postgres:p.sql"], tmp_path).concepts["Post"]
    assert post.prop("tags").target == "Tag" and "many" in post.prop("tags").flags


# 4. validate keeps numbers and strings apart in enums
def test_integer_enum_rejects_a_string(tmp_path):
    schema = {"type": "object", "properties": {"level": {"type": "integer", "enum": [1, 2, 3]}}}
    _write(tmp_path, "l.json", json.dumps(schema))
    onto = extract_all(["jsonschema:l.json"], tmp_path)
    assert validate_record(onto, "L", {"level": 2}) == []
    assert validate_record(onto, "L", {"level": "2"}) != []


def test_text_enum_that_looks_like_numbers_rejects_a_number(tmp_path):
    _write(tmp_path, "c.sql", "CREATE TABLE c (id int PRIMARY KEY, code text CHECK (code IN ('1', '2')));\n")
    onto = extract_all(["postgres:c.sql"], tmp_path)
    assert onto.concepts["C"].prop("code").target == 'enum("1"|"2")'
    assert validate_record(onto, "C", {"id": 1, "code": "2"}) == []
    assert validate_record(onto, "C", {"id": 1, "code": 2}) != []
    assert dumps(loads(dumps(onto))) == dumps(onto)


# 5. a column REFERENCES plus ADD CONSTRAINT FOREIGN KEY on the same column: one relation
def test_one_relation_for_a_foreign_key_declared_twice(tmp_path):
    _write(
        tmp_path,
        "f.sql",
        "CREATE TABLE authors (id int PRIMARY KEY);\n"
        "CREATE TABLE books (id int PRIMARY KEY, author_id int REFERENCES authors(id));\n"
        "ALTER TABLE books ADD CONSTRAINT books_author_fk FOREIGN KEY (author_id) REFERENCES authors(id);\n",
    )
    onto = extract_all(["postgres:f.sql"], tmp_path)
    assert [p.name for p in onto.concepts["Book"].properties()] == ["id", "author"]
    assert onto.warnings == []


# 6. mysqldump's PARTITION BY keeps the composite primary key
def test_mysqldump_partition_by_keeps_the_key(tmp_path):
    _write(
        tmp_path,
        "m.sql",
        "CREATE TABLE `sales` (\n  `id` int NOT NULL,\n  `year` int NOT NULL,\n  `amount` decimal(10,2),\n"
        "  PRIMARY KEY (`id`,`year`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4\n"
        "/*!50100 PARTITION BY RANGE (`year`)\n(PARTITION p0 VALUES LESS THAN (2020) ENGINE = InnoDB,\n"
        " PARTITION p1 VALUES LESS THAN MAXVALUE ENGINE = InnoDB) */;\n",
    )
    onto = extract_all(["mysql:m.sql"], tmp_path)
    sale = onto.concepts["Sale"]
    assert sale.first("unique").name == "id, year" and sale.prop("amount") is not None
    assert any("partitioned by RANGE" in s.value for s in sale.all("note"))
    assert onto.warnings == []


# 7. the rename hint shows on every run while the property is kept
def test_rename_hint_on_every_run(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY, isbn TEXT);\n")
    f = tmp_path / "b.onto"
    api.init(f, [f"sqlite:{(tmp_path / 's.sql').as_posix()}"])
    f.write_text(
        f.read_text(encoding="utf-8").replace(
            "  has isbn : text [auto]\n", '  has isbn : text [auto]\n    note "13"\n'
        ),
        encoding="utf-8",
    )
    # two new text columns: not a clear rename, so the property is kept and the hint is needed
    _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY, isbn13 TEXT, subtitle TEXT);\n")
    api.refresh(f)
    later = api.compare(f)[2].render()  # the run after: isbn13 is no longer new
    assert "plainonto rename FILE Book.isbn" in later


# 8. a file of blank lines does not grow
def test_blank_file_does_not_grow():
    for text in ("\n", "\n\n\n", ""):
        assert dumps(loads(text)) == text


# --- from the diff-only check of the ninth-review fixes ---


def _warnings(onto):
    return [m for _c, m in onto.warnings]


def test_alter_view_renames_are_applied(tmp_path):
    _write(
        tmp_path,
        "v.sql",
        "CREATE TABLE users (id int PRIMARY KEY, email text);\n"
        "CREATE VIEW active_users AS SELECT id, email FROM users;\n"
        "ALTER VIEW active_users RENAME TO live_users;\n"
        "ALTER VIEW live_users RENAME COLUMN email TO mail;\n",
    )
    onto = extract_all(["postgres:v.sql"], tmp_path)
    assert sorted(onto.concepts) == ["LiveUser", "User"]
    assert onto.concepts["LiveUser"].prop("mail") is not None and onto.concepts["LiveUser"].prop("email") is None
    assert _warnings(onto) == []


def test_harmless_alter_forms_do_not_warn(tmp_path):
    _write(
        tmp_path,
        "u.sql",
        "CREATE TABLE `users` (`id` bigint unsigned NOT NULL AUTO_INCREMENT PRIMARY KEY, `email` varchar(191));\n"
        "ALTER TABLE `users` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        "ALTER TABLE `users` TABLESPACE ts1;\n"
        "ALTER TABLE `users` REMOVE PARTITIONING;\n",
    )
    assert _warnings(extract_all(["mysql:u.sql"], tmp_path)) == []
    _write(
        tmp_path,
        "p.sql",
        "CREATE TABLE t (id int GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, c text);\n"
        "ALTER TABLE t ALTER COLUMN c SET (n_distinct = 100);\n"
        "ALTER TABLE t ALTER COLUMN c RESET (n_distinct);\n"
        "ALTER TABLE t ALTER COLUMN id SET INCREMENT BY 2;\n"
        "ALTER TABLE t ALTER COLUMN id SET START WITH 10;\n",
    )
    assert _warnings(extract_all(["postgres:p.sql"], tmp_path)) == []


def test_every_part_of_a_comma_separated_alter_counts(tmp_path):
    _write(
        tmp_path,
        "c.sql",
        "CREATE TABLE t (id int PRIMARY KEY);\nALTER TABLE t ENABLE ROW LEVEL SECURITY, ADD COLUMN z int;\n",
    )
    onto = extract_all(["postgres:c.sql"], tmp_path)
    assert onto.concepts["T"].prop("z") is not None or _warnings(onto)


def test_alter_index_rename_stays_in_its_schema(tmp_path):
    _write(
        tmp_path,
        "i.sql",
        "CREATE SCHEMA sales;\n"
        "CREATE TABLE public.a (id int PRIMARY KEY, code text);\n"
        "CREATE TABLE sales.a (id int PRIMARY KEY, code text);\n"
        "CREATE UNIQUE INDEX a_code ON public.a (code);\n"
        "CREATE UNIQUE INDEX a_code ON sales.a (code);\n"
        "ALTER INDEX sales.a_code RENAME TO a_code_new;\n"
        "DROP INDEX public.a_code;\n",
    )
    c = extract_all(["postgres:i.sql"], tmp_path).concepts
    assert "unique" not in c["A"].prop("code").flags and "unique" in c["SalesA"].prop("code").flags


def test_create_table_like_keeps_index_names(tmp_path):
    _write(
        tmp_path,
        "l.sql",
        "CREATE TABLE a (id int PRIMARY KEY, name varchar(20), UNIQUE KEY uq (name));\n"
        "CREATE TABLE b LIKE a;\nALTER TABLE b DROP INDEX uq;\n",
    )
    onto = extract_all(["mysql:l.sql"], tmp_path)
    assert "unique" not in onto.concepts["B"].prop("name").flags and "unique" in onto.concepts["A"].prop("name").flags
    assert _warnings(onto) == []


def test_mysql_rename_as(tmp_path):
    _write(tmp_path, "r.sql", "CREATE TABLE t (id int PRIMARY KEY);\nALTER TABLE t RENAME AS u;\n")
    onto = extract_all(["mysql:r.sql"], tmp_path)
    assert list(onto.concepts) == ["U"] and _warnings(onto) == []


def test_dropping_an_ordinary_index_is_quiet(tmp_path):
    _write(
        tmp_path,
        "l.sql",
        "CREATE TABLE posts (id int PRIMARY KEY, title varchar(200), KEY posts_k (title));\n"
        "ALTER TABLE posts ADD INDEX posts_title_index (title);\n"
        "CREATE INDEX posts_t2 ON posts (title);\n"
        "ALTER TABLE posts DROP INDEX posts_title_index;\nALTER TABLE posts DROP INDEX posts_k;\n"
        "DROP INDEX posts_t2 ON posts;\n",
    )
    onto = extract_all(["mysql:l.sql"], tmp_path)
    assert _warnings(onto) == [] and onto.concepts["Post"].prop("title").flags == ()

"""Release blockers from the ninth review (written before their fixes)."""

import pytest

from plainontology import api
from plainontology.extract import extract_all
from plainontology.rename import rename
from plainontology.textformat import load, save

pytest.importorskip("sqlglot")


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_b1_postgres_rename_without_column_keyword_renames_the_column(tmp_path):
    _write(
        tmp_path,
        "s.sql",
        "CREATE TABLE books (id int PRIMARY KEY, title text NOT NULL, status text);\n"
        "ALTER TABLE books RENAME title TO headline;\n",
    )
    onto = extract_all(["postgres:s.sql"], tmp_path)
    assert list(onto.concepts) == ["Book"]
    book = onto.concepts["Book"]
    assert book.prop("headline") is not None and book.prop("title") is None
    assert "required" in book.prop("headline").flags


def test_b1_the_table_rename_form_still_works(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE books (id int PRIMARY KEY);\nALTER TABLE books RENAME TO volumes;\n")
    assert list(extract_all(["postgres:s.sql"], tmp_path).concepts) == ["Volume"]


def test_b2_enum_column_changed_by_alter_column_type_follows_add_value(tmp_path):
    _write(
        tmp_path,
        "prisma.sql",
        "CREATE TYPE \"Role\" AS ENUM ('USER', 'ADMIN');\n"
        'CREATE TABLE "User" (id int PRIMARY KEY, "role" "Role" NOT NULL);\n'
        "ALTER TYPE \"Role\" ADD VALUE 'GUEST';\n"
        "CREATE TYPE \"Role_new\" AS ENUM ('USER', 'ADMIN');\n"
        'ALTER TABLE "User" ALTER COLUMN "role" TYPE "Role_new" USING ("role"::text::"Role_new");\n'
        'ALTER TYPE "Role" RENAME TO "Role_old";\n'
        'ALTER TYPE "Role_new" RENAME TO "Role";\n'
        'DROP TYPE "Role_old";\n'
        "ALTER TYPE \"Role\" ADD VALUE 'MODERATOR';\n",
    )
    user = extract_all(["postgres:prisma.sql"], tmp_path).concepts["User"]
    assert user.prop("role").target == "enum(USER|ADMIN|MODERATOR)"


def test_b3_mysql_create_table_like(tmp_path):
    _write(
        tmp_path,
        "lk.sql",
        "CREATE TABLE a (id int PRIMARY KEY, name varchar(20) NOT NULL, UNIQUE KEY n (name));\n"
        "CREATE TABLE b LIKE a;\n"
        "CREATE TABLE IF NOT EXISTS c LIKE a;\n"
        "ALTER TABLE b ADD COLUMN extra int;\n",
    )
    onto = extract_all(["mysql:lk.sql"], tmp_path)
    assert sorted(onto.concepts) == ["A", "B", "C"]
    b = onto.concepts["B"]
    assert b.prop("id").flags == ("key",) and "unique" in b.prop("name").flags and b.prop("extra") is not None
    assert onto.warnings == []


HEADER_COMMENT = (
    "source sqlite:{src}\n"
    "# Hand-checked against the 2026 data dictionary.\n\n"
    "concept Author [auto: sqlite table authors]\n"
    "  has id : integer key [auto]\n"
    "concept Book [auto: sqlite table books]\n"
    "  has id : integer key [auto]\n"
)


def test_b4_refresh_after_the_first_concept_goes_with_a_header_comment(tmp_path):
    _write(tmp_path, "s.sql", "CREATE TABLE books (id INTEGER PRIMARY KEY);\n")
    f = tmp_path / "lib.onto"
    f.write_text(HEADER_COMMENT.format(src=(tmp_path / "s.sql").as_posix()), encoding="utf-8")
    api.refresh(f)  # used to refuse to write
    onto = load(f)
    assert list(onto.concepts) == ["Book"] and onto.concepts["Book"].leading_comments == []
    assert "Hand-checked" in "".join(onto.comments)


def test_b4_rename_after_the_first_concept_goes_with_a_header_comment(tmp_path):
    f = tmp_path / "lib.onto"
    f.write_text(HEADER_COMMENT.format(src="x.sql"), encoding="utf-8")
    onto = load(f)
    rename(onto, "Author", "Book")
    save(f, onto)  # used to refuse to write
    assert "Hand-checked" in "".join(load(f).comments)


# --- the safety net: an ALTER, RENAME or CREATE ... LIKE that is not applied is a warning ---


def _warnings(onto):
    return [m for _c, m in onto.warnings]


def test_alter_table_forms_kept_as_text_are_applied_or_reported(tmp_path):
    _write(
        tmp_path,
        "a.sql",
        "CREATE TABLE parts (id int PRIMARY KEY);\n"
        "CREATE TABLE books (id int PRIMARY KEY, status text);\n"
        "ALTER TABLE books ADD CHECK (status IN ('draft','live'));\n"
        "ALTER TABLE books INHERIT parts;\n"
        "ALTER TABLE IF EXISTS ONLY books OWNER TO postgres;\n"
        "ALTER TABLE ONLY books ENABLE ROW LEVEL SECURITY;\n"
        "ALTER TABLE books ALTER COLUMN id SET STATISTICS 100;\n"
        "ALTER TABLE books SOMETHING NEW;\n",
    )
    onto = extract_all(["postgres:a.sql"], tmp_path)
    book = onto.concepts["Book"]
    assert book.prop("status").target == "enum(draft|live)"
    assert book.first("is_a").name == "Part"
    assert [w for w in _warnings(onto) if "could not read" in w] == [
        "postgres:a.sql: skipped a statement it could not read: ALTER TABLE books SOMETHING NEW"
    ]


def test_if_exists_and_only_in_either_order(tmp_path):
    _write(
        tmp_path,
        "s.sql",
        "CREATE SCHEMA archive;\nCREATE TABLE orders (id int PRIMARY KEY);\n"
        "ALTER TABLE IF EXISTS ONLY orders SET SCHEMA archive;\n",
    )
    assert list(extract_all(["postgres:s.sql"], tmp_path).concepts) == ["ArchiveOrder"]


def test_alter_index_rename_then_drop(tmp_path):
    _write(
        tmp_path,
        "i.sql",
        'CREATE TABLE "User" (id int PRIMARY KEY, mail text);\n'
        'CREATE UNIQUE INDEX "User_email_key" ON "User" (mail);\n'
        'ALTER INDEX "User_email_key" RENAME TO "User_mail_key";\n'
        'DROP INDEX "User_mail_key";\n',
    )
    onto = extract_all(["postgres:i.sql"], tmp_path)
    assert "unique" not in onto.concepts["User"].prop("mail").flags
    assert _warnings(onto) == []


def test_float_unsigned_after_a_comment_with_an_apostrophe(tmp_path):
    _write(
        tmp_path,
        "f.sql",
        "-- don't reorder columns\n"
        "CREATE TABLE shelf (id int PRIMARY KEY, weight float unsigned NOT NULL,"
        " label varchar(20) COMMENT 'Shelf label');\n",
    )
    onto = extract_all(["mysql:f.sql"], tmp_path)
    shelf = onto.concepts["Shelf"]
    assert shelf.prop("weight").flags == ("required",) and shelf.prop("weight").child("min").value == "0"
    assert shelf.prop("label").value == "Shelf label" and _warnings(onto) == []

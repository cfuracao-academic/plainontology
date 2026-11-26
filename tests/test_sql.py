import sqlite3
from pathlib import Path

import pytest

from plainontology.cli import main
from plainontology.extract import extract
from plainontology.extract.sqldump import tables_from_ddl
from plainontology.extract.sqllive import resolve_url, tables_from_database
from plainontology.extract.sqlmodel import map_type, tables_to_concepts
from plainontology.textformat import load

from .conftest import EXAMPLES


def _lines(concepts):
    return {c.name: [s.body() for s in c.statements] for c in concepts}


@pytest.mark.parametrize("declared,expected", [
    ("character varying(13)", "text"), ("timestamp with time zone", "datetime"), ("time without time zone", "text"),
    ("bigserial", "integer"), ("numeric(10,2)", "decimal"), ("double precision", "decimal"), ("jsonb", "json"),
    ("bytea", "binary"), ("tinyint(1)", "boolean"), ("tinyint(4)", "integer"), ("interval", "text"),
    ("point", "text"), ("uuid", "text"), ("longblob", "binary"), ("datetime(6)", "datetime"), ("bit(1)", "boolean"),
])
def test_map_type(declared, expected):
    assert map_type(declared) == expected


def test_postgres_dump():
    concepts = _lines(extract("postgres:library.postgres.sql", EXAMPLES))
    assert list(concepts) == ["Author", "Book", "Tag", "Loan"]  # book_tags is a join table
    book = concepts["Book"]
    assert "has id : integer key" in book  # primary key from ALTER TABLE
    assert "has isbn : text unique" in book  # UNIQUE constraint from ALTER TABLE
    assert "has keywords : text many" in book  # text[]
    assert "author -> Author required" in book  # FOREIGN KEY from ALTER TABLE
    assert "tags -> Tag many" in book
    assert "has label : text required unique" in concepts["Tag"]  # CREATE UNIQUE INDEX
    assert "has status : enum(open|returned|lost) required" in concepts["Loan"]  # CREATE TYPE ... AS ENUM


def test_mysql_dump():
    concepts = _lines(extract("mysql:library.mysql.sql", EXAMPLES))
    assert list(concepts) == ["Author", "Book", "Tag", "Loan"]
    assert "has in_print : boolean" in concepts["Book"]  # tinyint(1)
    assert "has isbn : text unique" in concepts["Book"]  # UNIQUE KEY
    assert "has status : enum(open|returned|lost) required" in concepts["Loan"]


def test_all_dialects_agree_on_the_shared_schema():
    sqlite = _lines(extract("sqlite:library.sql", EXAMPLES))
    postgres = _lines(extract("postgres:library.postgres.sql", EXAMPLES))
    mysql = _lines(extract("mysql:library.mysql.sql", EXAMPLES))
    for name in ("Author", "Tag"):
        assert sqlite[name] == postgres[name] == mysql[name]
    assert [x for x in postgres["Book"] if "keywords" not in x] == sqlite["Book"] == mysql["Book"]
    assert [x for x in postgres["Loan"] if "status" not in x] == sqlite["Loan"]


def test_inline_constraints_and_noise_are_handled():
    ddl = """
    SET search_path = public;
    CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.at := now(); RETURN NEW; END $$;
    CREATE TABLE people (id bigserial PRIMARY KEY, email text NOT NULL UNIQUE, boss_id bigint REFERENCES people(id));
    COPY people (id, email, boss_id) FROM stdin;
    1\ta@example.org\t\\N
    \\.
    INSERT INTO people VALUES (2, 'b@example.org', 1);
    """
    concepts = _lines(tables_to_concepts(tables_from_ddl(ddl, "postgres"), "postgres"))
    assert concepts == {"Person": ["has id : integer key", "has email : text required unique", "boss -> Person"]}


def test_live_reader_through_sqlalchemy(tmp_path):
    db = tmp_path / "lib.db"
    con = sqlite3.connect(db)
    con.executescript((EXAMPLES / "library.sql").read_text(encoding="utf-8"))
    con.close()
    live = _lines(tables_to_concepts(tables_from_database(f"sqlite:///{db.as_posix()}"), "sqlite"))
    from_file = _lines(extract("sqlite:library.sql", EXAMPLES))
    for name in ("Author", "Tag", "Loan"):
        assert live[name] == from_file[name]
    assert "tags -> Tag many" in live["Book"] and "author -> Author required" in live["Book"]


def test_urls_never_store_passwords(examples, monkeypatch):
    monkeypatch.chdir(examples)
    with pytest.raises(ValueError, match="password"):
        resolve_url("postgresql://me:secret@localhost/db")
    assert main(["init", "x.onto", "--from", "postgres:postgresql://me:secret@localhost/db"]) == 2
    assert not Path("x.onto").exists()
    assert resolve_url("postgresql://me@localhost/db") == "postgresql+psycopg://me@localhost/db"
    assert resolve_url("mysql://me@localhost/db") == "mysql+pymysql://me@localhost/db"


def test_env_source_is_stored_as_given(examples, monkeypatch):
    db = examples / "lib.db"
    con = sqlite3.connect(db)
    con.executescript((examples / "library.sql").read_text(encoding="utf-8"))
    con.close()
    monkeypatch.chdir(examples)
    monkeypatch.setenv("LIBRARY_DB", f"sqlite:///{db.as_posix()}")
    assert main(["init", "live.onto", "--from", "postgres:env:LIBRARY_DB"]) == 0
    onto = load(examples / "live.onto")
    assert onto.sources == ["postgres:env:LIBRARY_DB"]
    assert "Author" in onto.concepts
    monkeypatch.delenv("LIBRARY_DB")
    assert main(["drift", "live.onto"]) == 2  # clear error, not a crash

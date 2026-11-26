import sqlite3
from pathlib import Path

import pytest

from plainontology.cli import main
from plainontology.extract import extract
from plainontology.extract.sqlcheck import analyse
from plainontology.extract.sqldump import tables_from_ddl
from plainontology.extract.sqllive import resolve_url, tables_from_database
from plainontology.extract.sqlmodel import map_type, tables_to_concepts
from plainontology.textformat import load

from .conftest import EXAMPLES


def _lines(concepts, children=True):
    out = {}
    for c in concepts:
        lines = []
        for s in c.statements:
            lines.append(s.body())
            if children:
                lines += ["  " + ch.body() for ch in s.children]
        out[c.name] = lines
    return out


def _structure(concepts):
    """Property lines without descriptions or children: what every dialect must agree on."""
    return {c.name: [f"{s.kind} {s.name} {s.target} {' '.join(s.flags)}" for s in c.properties()] for c in concepts}


@pytest.mark.parametrize("declared,expected", [
    ("character varying(13)", "text"), ("timestamp with time zone", "datetime"), ("time without time zone", "text"),
    ("bigserial", "integer"), ("numeric(10,2)", "decimal"), ("double precision", "decimal"), ("jsonb", "json"),
    ("bytea", "binary"), ("tinyint(1)", "boolean"), ("tinyint(4)", "integer"), ("interval", "text"),
    ("point", "text"), ("uuid", "text"), ("longblob", "binary"), ("datetime(6)", "datetime"), ("bit(1)", "boolean"),
])
def test_map_type(declared, expected):
    assert map_type(declared) == expected


@pytest.mark.parametrize("sql,expected", [
    ("pages > 0", [("min", "pages", 0, True)]),
    ("year BETWEEN 1900 AND 2100", [("min", "year", 1900, False), ("max", "year", 2100, False)]),
    ("((year >= 1900) AND (year <= 2100))", [("min", "year", 1900, False), ("max", "year", 2100, False)]),
    ("status IN ('a', 'b')", [("enum", "status", ["a", "b"], False)]),
    ("((status)::text = ANY ((ARRAY['a'::character varying, 'b'::character varying])::text[]))",
     [("enum", "status", ["a", "b"], False)]),
    ("char_length(code) <= 20", [("length_max", "code", 20, False)]),
    ("((code)::text ~ '^C-[0-9]+$'::text)", [("pattern", "code", "^C-[0-9]+$", False)]),
    ("0 < price", [("min", "price", 0, True)]),
])
def test_check_analysis(sql, expected):
    assert [(f.kind, f.column, f.value, f.exclusive) for f in analyse(sql, "postgres")] == expected


def test_unrecognised_check_is_kept_as_note():
    (finding,) = analyse("((returned_at IS NULL) OR (returned_at >= loaned_at))", "postgres")
    assert finding.kind == "note" and "returned_at >= loaned_at" in finding.value


def test_postgres_dump():
    concepts = _lines(extract("postgres:library.postgres.sql", EXAMPLES))
    assert list(concepts) == ["Author", "Book", "Award", "BookAward", "Tag", "Loan", "ArchiveLoan", "OpenLoan"]
    book = concepts["Book"]
    assert 'definition "A title the library holds; one record per edition."' in book  # COMMENT ON TABLE
    assert 'has isbn : text unique "ISBN-13 without hyphens."' in book  # COMMENT ON COLUMN + UNIQUE
    assert "  length ..13" in book  # varchar(13)
    assert "has keywords : text many" in book  # text[]
    assert book[book.index("has pages : integer") + 1] == "  min 1"  # CHECK (pages > 0)
    assert "awards -> Award many" in book  # join table with data
    assert "tags -> Tag many" in book  # pure join table
    award = concepts["BookAward"]  # the join table with data stays a concept
    assert award[:3] == ["has year : integer required", "  min 1900", "  max 2100"]
    assert "unique (book, award)" in award
    loan = concepts["Loan"]
    assert loan[loan.index('has member : text required "Library card number."') + 2] == '  pattern "^C-[0-9]+$"'  # domain
    assert "has status : enum(open|returned|lost) required" in loan  # CREATE TYPE ... AS ENUM
    assert 'note "trigger loans_touch: BEFORE INSERT, UPDATE"' in loan
    assert any(line.startswith('note "check: ') for line in loan)  # a check that is not a rule
    assert concepts["ArchiveLoan"] == ["has id : integer key", "has closed_at : datetime required", "book -> Book required"]
    view = concepts["OpenLoan"]
    assert view[:2] == ['definition "Loans not yet returned."', 'note "database view (read-only)"']
    assert "has book_title : text" in view


def test_mysql_dump():
    concepts = _lines(extract("mysql:library.mysql.sql", EXAMPLES))
    assert list(concepts) == ["Author", "Book", "Tag", "Loan", "OpenLoan"]  # the view from /*!50001 ... */
    book = concepts["Book"]
    assert "has in_print : boolean" in book  # tinyint(1)
    assert 'definition "A title the library holds; one record per edition."' in book  # table COMMENT=
    assert 'has isbn : text unique "ISBN-13 without hyphens."' in book  # column COMMENT
    assert book[book.index("has pages : integer") + 1] == "  min 1"
    loan = concepts["Loan"]
    assert "has status : enum(open|returned|lost) required" in loan
    assert 'note "trigger loans_touch: BEFORE UPDATE"' in loan  # trigger hidden in /*!50003 ... */
    assert "has book_title : text" in concepts["OpenLoan"]


def test_all_dialects_agree_on_the_shared_schema():
    sqlite = _structure(extract("sqlite:library.sql", EXAMPLES))
    postgres = _structure(extract("postgres:library.postgres.sql", EXAMPLES))
    mysql = _structure(extract("mysql:library.mysql.sql", EXAMPLES))
    for name in ("Author", "Tag"):
        assert sqlite[name] == postgres[name] == mysql[name]
    for name in ("Book", "Loan"):
        assert set(sqlite[name]) <= set(postgres[name])
        assert set(sqlite[name]) <= set(mysql[name])


def test_sqlite_checks_views_and_triggers():
    ddl = """
    CREATE TABLE items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('a', 'b')),
                        qty INTEGER CHECK (qty >= 0), code VARCHAR(8), UNIQUE (kind, code));
    CREATE VIEW cheap AS SELECT id, code FROM items WHERE qty < 5;
    CREATE TRIGGER items_log AFTER INSERT ON items BEGIN SELECT 1; END;
    """
    db = sqlite3.connect(":memory:")
    db.executescript(ddl)
    from plainontology.extract.sqlite import read_tables

    concepts = _lines(tables_to_concepts(read_tables(db), "sqlite"))
    assert concepts["Item"] == [
        "has id : integer key", "has kind : enum(a|b) required", "has qty : integer", "  min 0",
        "has code : text", "  length ..8", "unique (kind, code)", 'note "trigger items_log: AFTER INSERT"',
    ]
    assert concepts["Cheap"][0] == 'note "database view (read-only)"'


def test_noise_in_dumps_is_skipped():
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
    live = _structure(tables_to_concepts(tables_from_database(f"sqlite:///{db.as_posix()}"), "sqlite"))
    from_file = _structure(extract("sqlite:library.sql", EXAMPLES))
    for name in ("Author", "Tag", "Loan"):
        assert live[name] == from_file[name]
    assert "rel tags Tag many" in live["Book"]


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

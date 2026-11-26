"""Load the example dumps into real PostgreSQL and MySQL servers, read them back
live, and check the result equals reading the dump file.

Skipped unless PLAINONTO_TEST_POSTGRES_URL / PLAINONTO_TEST_MYSQL_URL are set
(CI starts both servers; see .github/workflows/ci.yml).
"""

import os
import re

import pytest

from plainontology.extract import extract
from plainontology.extract.sqllive import with_driver

from .conftest import EXAMPLES


def _lines(concepts):
    """Lines per concept, with their child lines, in a stable order: a live database lists
    tables alphabetically while a dump keeps file order."""
    out = {}
    for c in concepts:
        lines = []
        for s in c.statements:
            lines.append(s.body())
            lines += [f"{s.name}/{ch.body()}" for ch in s.children]
        out[c.name] = sorted(lines)
    return out


def _load(url: str, sql: str, split: bool, reset: list[str]) -> None:
    sa = pytest.importorskip("sqlalchemy")
    engine = sa.create_engine(with_driver(url))
    try:
        with engine.begin() as conn:
            for statement in reset:
                conn.exec_driver_sql(statement)
            if split:
                # DELIMITER is a mysql client command, not SQL the server understands.
                sql = re.sub(r"^DELIMITER\s+\S+\s*$", "", sql, flags=re.M).replace(";;", ";")
                for chunk in re.split(r";\s*\n", sql):
                    body = "\n".join(l for l in chunk.splitlines() if not l.startswith("--")).strip()
                    if body:
                        conn.exec_driver_sql(body)
            else:
                conn.exec_driver_sql(sql)
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("PLAINONTO_TEST_POSTGRES_URL"), reason="no PostgreSQL server")
def test_postgres_live_matches_dump():
    var = "PLAINONTO_TEST_POSTGRES_URL"
    sql = (EXAMPLES / "library.postgres.sql").read_text(encoding="utf-8")
    _load(os.environ[var], sql, split=False, reset=["DROP SCHEMA IF EXISTS public CASCADE", "CREATE SCHEMA public"])
    live = _lines(extract(f"postgres:env:{var}", EXAMPLES))
    dump = _lines(extract("postgres:library.postgres.sql", EXAMPLES))
    assert live == dump


@pytest.mark.skipif(not os.environ.get("PLAINONTO_TEST_MYSQL_URL"), reason="no MySQL server")
def test_mysql_live_matches_dump():
    var = "PLAINONTO_TEST_MYSQL_URL"
    sql = (EXAMPLES / "library.mysql.sql").read_text(encoding="utf-8")
    _load(os.environ[var], sql, split=True, reset=[])
    live = _lines(extract(f"mysql:env:{var}", EXAMPLES))
    dump = _lines(extract("mysql:library.mysql.sql", EXAMPLES))
    assert live == dump


INHERITANCE = """\
CREATE TABLE public.items (
    id integer PRIMARY KEY,
    code text NOT NULL CHECK (char_length(code) <= 5)
);
CREATE TABLE public.ebooks (
    file_url text NOT NULL
) INHERITS (public.items);
CREATE TABLE public.item_copy (LIKE public.items INCLUDING ALL);
"""


@pytest.mark.skipif(not os.environ.get("PLAINONTO_TEST_POSTGRES_URL"), reason="no PostgreSQL server")
def test_postgres_inheritance_live_matches_dump(tmp_path):
    var = "PLAINONTO_TEST_POSTGRES_URL"
    reset = ["DROP SCHEMA IF EXISTS archive CASCADE", "DROP SCHEMA IF EXISTS public CASCADE", "CREATE SCHEMA public"]
    _load(os.environ[var], INHERITANCE, split=False, reset=reset)
    (tmp_path / "inh.sql").write_text(INHERITANCE, encoding="utf-8")
    live = _lines(extract(f"postgres:env:{var}", tmp_path))
    dump = _lines(extract("postgres:inh.sql", tmp_path))
    assert "is_a Item [auto]" in dump["Ebook"]
    # LIKE leaves a note in the dump that a live database cannot know about
    dump["ItemCopy"] = [line for line in dump["ItemCopy"] if "(LIKE)" not in line]
    assert live == dump

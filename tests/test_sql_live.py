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
    return {c.name: [s.body() for s in c.statements] for c in concepts}


def _load(url: str, sql: str, split: bool, reset: list[str]) -> None:
    sa = pytest.importorskip("sqlalchemy")
    engine = sa.create_engine(with_driver(url))
    try:
        with engine.begin() as conn:
            for statement in reset:
                conn.exec_driver_sql(statement)
            if split:
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

"""Regressions found on real schemas and in the third review."""

import pytest

pytest.importorskip("sqlglot")

from plainontology.extract.sqldump import tables_from_ddl

BASE = "CREATE TABLE actor (actor_id INT PRIMARY KEY, first_name VARCHAR(45));\n"


@pytest.mark.parametrize(
    "header",
    [
        "CREATE DEFINER=CURRENT_USER SQL SECURITY INVOKER VIEW",
        "CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`localhost` SQL SECURITY DEFINER VIEW",
        "CREATE OR REPLACE DEFINER='app'@'%' VIEW",
    ],
)
def test_mysql_view_modifiers_are_read(header):
    warnings: list[str] = []
    sql = BASE + f"{header} actor_info AS SELECT a.actor_id, a.first_name FROM actor a;\n"
    tables = {t.name: t for t in tables_from_ddl(sql, "mysql", warnings)}
    assert "actor_info" in tables and tables["actor_info"].kind == "view"
    assert [c.name for c in tables["actor_info"].columns] == ["actor_id", "first_name"]
    assert warnings == []


def test_postgres_materialized_view_with_no_data_is_read():
    warnings: list[str] = []
    sql = BASE + "CREATE MATERIALIZED VIEW public.names AS\n SELECT a.first_name FROM actor a\n  WITH NO DATA;\n"
    tables = {t.name: t for t in tables_from_ddl(sql, "postgres", warnings)}
    assert "names" in tables and tables["names"].kind == "view"
    assert warnings == []


def test_unreadable_create_view_is_a_warning_not_silence():
    warnings: list[str] = []
    sql = BASE + "CREATE VIEW v AS SELECT FROM WHERE ))) nonsense;\n"
    tables_from_ddl(sql, "postgres", warnings)
    assert any("could not read" in w and "CREATE VIEW v" in w for w in warnings), warnings


def test_functions_and_procedures_are_not_flagged():
    warnings: list[str] = []
    sql = BASE + (
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN CREATE TABLE x (a int); RETURN 1; END $$ LANGUAGE plpgsql;\n"
    )
    tables_from_ddl(sql, "postgres", warnings)
    assert not any("could not read" in w for w in warnings), warnings

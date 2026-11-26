"""Real-world dumps: nothing may vanish silently, and odd syntax must not crash.

These cases come from running PlainOntology on the public Pagila schema, where
a PostgreSQL 18 virtual column hid a whole table, function bodies crashed the
tokenizer, and 55 partitions showed up as separate concepts.
"""

from plainontology.extract import extract_all
from plainontology.extract.sqldump import tables_from_ddl
from plainontology.extract.sqlmodel import tables_to_concepts


def _lines(ddl, dialect="postgres", warnings=None):
    tables = tables_from_ddl(ddl, dialect, warnings)
    return {c.name: [s.body() for s in c.statements] for c in tables_to_concepts(tables, dialect)}


def test_virtual_generated_column():
    ddl = """CREATE TABLE public.film (
        film_id integer NOT NULL,
        length smallint,
        length_hours numeric(4,2) GENERATED ALWAYS AS (round(length / 60.0, 2)) VIRTUAL
    );"""
    assert _lines(ddl)["Film"] == [
        "has film_id : integer required",
        "has length : integer",
        "has length_hours : decimal",
    ]


def test_function_bodies_do_not_crash():
    ddl = """CREATE FUNCTION public.film_in_stock(p_film_id integer, p_store_id integer, OUT p_film_count integer)
    RETURNS SETOF integer LANGUAGE sql AS $_$
     SELECT inventory_id FROM inventory WHERE film_id = $1 AND store_id = $2
     AND inventory_in_stock(inventory_id);
$_$;

CREATE TABLE public.store (store_id integer NOT NULL);
"""
    assert _lines(ddl) == {"Store": ["has store_id : integer required"]}


def test_unreadable_column_is_skipped_with_a_warning_not_the_table():
    ddl = """SET x = 1;
CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $$ SELECT $1 $$;
CREATE TABLE public.shelf (
    id integer NOT NULL,
    position point WITH SOME (NONSENSE ,
    label text
);
"""
    warnings: list[str] = []
    concepts = _lines(ddl, warnings=warnings)
    assert "Shelf" in concepts and "has id : integer required" in concepts["Shelf"]
    assert any("could not read" in w for w in warnings)


def test_partitions_fold_into_their_parent():
    ddl = """CREATE TABLE public.payment (id integer NOT NULL, paid_on date NOT NULL, customer_id integer)
PARTITION BY RANGE (paid_on);
CREATE TABLE public.customer (id integer PRIMARY KEY);
CREATE TABLE public.payment_2026 (id integer NOT NULL, paid_on date NOT NULL, customer_id integer);
CREATE TABLE public.payment_2027 PARTITION OF public.payment FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
ALTER TABLE ONLY public.payment ATTACH PARTITION public.payment_2026 FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
ALTER TABLE ONLY public.payment_2026 ADD CONSTRAINT p_fk FOREIGN KEY (customer_id) REFERENCES public.customer(id);
"""
    concepts = _lines(ddl)
    assert set(concepts) == {"Payment", "Customer"}
    assert "customer -> Customer" in concepts["Payment"]
    assert concepts["Payment"][-1] == 'note "partitioned by RANGE (paid_on) into 2 tables"'


def test_skipped_statements_reach_the_report(tmp_path):
    (tmp_path / "s.sql").write_text(
        "CREATE TABLE public.a (id integer);\n"
        "COMMENT ON TABLE public.a IS 'unterminated;\n"
        "CREATE TABLE public.b (id integer);\n",
        encoding="utf-8",
    )
    onto = extract_all(["postgres:s.sql"], tmp_path)
    assert {"A", "B"} <= set(onto.concepts)
    assert any("postgres:s.sql: skipped a statement it could not read: COMMENT ON TABLE" in m for _, m in onto.warnings)

# Performance

Generated schemas, each table with 10 columns, a foreign key, a commented column and three CHECK constraints; every tenth table is a join table. Wall-clock seconds for one run, including Python start-up, on a laptop (Python 3.13, Windows, AMD x64).

| Command | 100 tables | 1,000 tables | 5,000 tables |
|---|---|---|---|
| `init` (PostgreSQL dump) | 0.33 s | 1.29 s | 6.81 s |
| `init` (SQLite) | 0.57 s | 1.35 s | 7.64 s |
| `refresh`, no change (PostgreSQL dump) | 0.33 s | 1.47 s | 7.27 s |
| `drift` (PostgreSQL dump) | 0.32 s | 1.45 s | 7.35 s |
| `check` | 0.14 s | 0.18 s | 0.37 s |
| `export` (Turtle, SHACL or LinkML) | 0.14 s | 0.20 s | 0.54 s or less |

The file has about 13.5 lines per table (67,505 lines at 5,000 tables). Time grows linearly with the number of tables.

Reproduce with `python benchmarks/bench.py 100 1000 5000` from the repository.

## Real schemas

CI also extracts two public sample databases at pinned versions: Pagila (PostgreSQL, 71 tables of which 55 are partitions, giving 24 concepts) and Sakila (MySQL, 16 tables and 7 views, giving 23 concepts). Each takes about 0.4 seconds, with no warnings and a clean `check --strict`.

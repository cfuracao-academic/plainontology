"""Time PlainOntology on large generated schemas.

    python benchmarks/bench.py            # 100 and 1000 tables
    python benchmarks/bench.py 5000       # a custom size

Each table has 10 columns (one varchar with a CHECK, one comment), a foreign
key to the table before it, and every tenth table is a join table. The same
schema is written as a SQLite .sql file and as a PostgreSQL dump. Timings are
wall-clock seconds for one run of each command, including Python start-up.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

COMMANDS = [
    ("init", ["init", "s.onto", "--from", "{source}", "--force"]),
    ("refresh (no change)", ["refresh", "s.onto"]),
    ("drift", ["drift", "s.onto"]),
    ("check", ["check", "s.onto"]),
    ("export turtle", ["export", "s.onto", "--to", "turtle", "-o", "s.ttl"]),
    ("export shacl", ["export", "s.onto", "--to", "shacl", "-o", "s.shapes.ttl"]),
    ("export linkml", ["export", "s.onto", "--to", "linkml", "-o", "s.yaml"]),
]


def schema(tables: int, dialect: str) -> str:
    q = '"' if dialect == "sqlite" else ""
    lines = []
    for i in range(tables):
        name = f"{q}table_{i:05d}{q}"
        if i % 10 == 9 and i >= 2:
            lines.append(
                f"CREATE TABLE {name} (left_id integer NOT NULL REFERENCES table_{i - 1:05d}(id), "
                f"right_id integer NOT NULL REFERENCES table_{i - 2:05d}(id), PRIMARY KEY (left_id, right_id));"
            )
            continue
        ref = f", parent_id integer REFERENCES table_{i - 1:05d}(id)" if i and (i - 1) % 10 != 9 else ""
        lines.append(
            f"CREATE TABLE {name} (id integer PRIMARY KEY, code varchar(12) NOT NULL UNIQUE "
            f"CHECK (length(code) >= 3), title text NOT NULL, amount numeric(10,2) CHECK (amount >= 0), "
            f"status varchar(10) CHECK (status IN ('new', 'open', 'closed')), created date, updated timestamp, "
            f"flag boolean, notes text{ref});"
        )
        if dialect == "postgres":
            lines.append(f"COMMENT ON COLUMN table_{i:05d}.title IS 'Human title of record {i}.';")
    return "\n".join(lines) + "\n"


def run(work: Path, args: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run([sys.executable, "-m", "plainontology", *args], cwd=work, check=True, capture_output=True)
    return time.perf_counter() - start


def main() -> None:
    sizes = [int(a) for a in sys.argv[1:]] or [100, 1000]
    print(f"Python {platform.python_version()} on {platform.system()} {platform.machine()}, {platform.processor() or ''}")
    header = "| Command | " + " | ".join(f"{d} {n} tables" for n in sizes for d in ("SQLite", "PostgreSQL")) + " |"
    print(header)
    print("|" + "---|" * (header.count("|") - 1))
    results: dict[str, list[str]] = {name: [] for name, _ in COMMANDS}
    lines_out = []
    for n in sizes:
        for dialect, kind, file in (("sqlite", "sqlite", "s.sql"), ("postgres", "postgres", "s.pg.sql")):
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                (work / file).write_text(schema(n, dialect), encoding="utf-8")
                for name, args in COMMANDS:
                    args = [a.replace("{source}", f"{kind}:{file}") for a in args]
                    results[name].append(f"{run(work, args):.2f} s")
                lines_out.append(f"{dialect} {n}: {len((work / 's.onto').read_text(encoding='utf-8').splitlines())} lines")
    for name, cells in results.items():
        print(f"| {name} | " + " | ".join(cells) + " |")
    print()
    print("; ".join(lines_out))


if __name__ == "__main__":
    main()

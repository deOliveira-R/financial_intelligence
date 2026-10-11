"""One-off copy of the SQLite database into PostgreSQL (October 2026 move).

Run as the app user on the server, with the PostgreSQL schema already at head (`fin-intel
migrate`) and every job stopped:

    .venv/bin/python deploy/sqlite_to_postgres.py data/fin_intel.db [table ...]

Tables are copied in foreign-key order with COPY, only the columns the models declare
(generated columns compute themselves); each table must arrive with the same row count.
Id sequences are then set past the copied ids. A table that already has rows is skipped,
so an interrupted run can be resumed after truncating the table it stopped in.
"""

import sqlite3
import sys
import time

from sqlalchemy import text

from fin_intel import models  # noqa: F401  (registers tables)
from fin_intel.db import Base, get_engine

BATCH = 50_000


def copy_table(source: sqlite3.Connection, engine, table) -> tuple[int, int]:
    columns = [c.name for c in table.columns if c.computed is None]
    present = {row[1] for row in source.execute(f'PRAGMA table_info("{table.name}")')}
    if not present:
        print(f"{table.name}: not in SQLite, skipped")
        return 0, -1
    missing = [c for c in columns if c not in present]
    if missing:
        raise SystemExit(f"{table.name}: columns missing in SQLite: {missing}")
    expected = source.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0]
    with engine.begin() as conn:
        if conn.execute(text(f'SELECT EXISTS (SELECT 1 FROM "{table.name}")')).scalar():
            print(f"{table.name}: already has rows, skipped")
            return expected, -1
    names = ", ".join(f'"{c}"' for c in columns)
    rows = source.execute(f'SELECT {names} FROM "{table.name}"')
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("SET TIME ZONE 'UTC'")  # SQLite kept UTC times without an offset
        with cursor.copy(f'COPY "{table.name}" ({names}) FROM STDIN') as copy:
            while batch := rows.fetchmany(BATCH):
                for row in batch:
                    copy.write_row(row)
        raw.commit()
    finally:
        raw.close()
    with engine.begin() as conn:
        copied = conn.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar()
    return expected, copied


def reset_sequences(engine) -> None:
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            key = list(table.primary_key)
            if len(key) != 1 or key[0].type.python_type is not int:
                continue
            conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', '{key[0].name}'), "
                    f'COALESCE((SELECT MAX("{key[0].name}") FROM "{table.name}"), 0) + 1, false)'
                )
            )


def main() -> None:
    source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    only = set(sys.argv[2:])
    engine = get_engine()
    problems = []
    for table in Base.metadata.sorted_tables:
        if only and table.name not in only:
            continue
        started = time.monotonic()
        expected, copied = copy_table(source, engine, table)
        took = time.monotonic() - started
        status = "skipped" if copied < 0 else "ok" if copied == expected else "MISMATCH"
        print(
            f"{table.name:32} {expected:>12,} -> {copied:>12,}  {status}  {took:,.0f}s", flush=True
        )
        if status == "MISMATCH":
            problems.append(table.name)
    reset_sequences(engine)
    print("sequences reset")
    if problems:
        raise SystemExit(f"row counts differ: {', '.join(problems)}")


if __name__ == "__main__":
    main()

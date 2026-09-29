"""Publish the gated marts from DuckDB to the served Postgres, then prove parity (design pin 5).

Runs only after `dbt build` passed (the DAG wires it downstream of the gates), so Postgres only ever holds a
gated build. Tables are written into `published_new` and swapped into `published` in one transaction: readers see
the old build or the new one, never a half-copied mix. After the swap, parity is checked by running the same
aggregates natively in both engines -- row counts, distinct keys, and sums of the money columns. Any mismatch fails
the task.
"""

from __future__ import annotations

import math
from pathlib import Path

import duckdb

TABLES = ("fct_standard_charges", "dim_charge_codes", "dim_modifiers", "rpt_source_conformance", "stg_hpt__files",
          "rpt_nppes_file_log")  # the 9.8M-row NPPES registry itself is not re-served: CMS publishes it (CP-DEC 010)

# (table, aggregate SQL valid in both DuckDB and Postgres)
PARITY = {
    "fct_standard_charges": "count(*), count(distinct charge_id), count(distinct item_id), "
                            "sum(negotiated_rate), sum(gross_charge), sum(median_amount), count(negotiated_rate)",
    "dim_charge_codes": "count(*), count(distinct item_id), count(code_family)",
    "dim_modifiers": "count(*)",
    "rpt_source_conformance": "count(*), sum(n)",
    "stg_hpt__files": "count(*), sum(records_read), sum(charge_rows)",
    "rpt_nppes_file_log": "count(*), sum(rows), sum(inserted), sum(updated), sum(deactivated), sum(unchanged)",
}


def _split(aggs: str) -> list[str]:
    """Split an aggregate list on top-level commas (not those inside parentheses)."""
    parts, depth, cur = [], 0, ""
    for ch in aggs:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def _close(a: object, b: object) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
    return int(a) == int(b)


def publish(warehouse: Path, dsn: str) -> bool:
    # in-memory session: the warehouse attached read-only, Postgres read-write (a read-only *connection* would make
    # every attachment read-only, Postgres included)
    con = duckdb.connect()
    con.sql("INSTALL postgres; LOAD postgres;")
    con.sql(f"ATTACH '{warehouse.as_posix()}' AS wh (READ_ONLY)")
    con.sql(f"ATTACH '{dsn}' AS pg (TYPE postgres)")
    con.sql("CALL postgres_execute('pg', 'DROP SCHEMA IF EXISTS published_new CASCADE; CREATE SCHEMA published_new;')")
    for t in TABLES:
        target = "files" if t == "stg_hpt__files" else t
        con.sql(f"CREATE TABLE pg.published_new.{target} AS SELECT * FROM wh.main.{t}")
        print(f"[publish] {target}: {con.sql(f'select count(*) from wh.main.{t}').fetchone()[0]:,} rows", flush=True)
    con.sql("CALL postgres_execute('pg', 'BEGIN; DROP SCHEMA IF EXISTS published CASCADE; "
            "ALTER SCHEMA published_new RENAME TO published; COMMIT;')")

    ok = True
    for t, aggs in PARITY.items():
        target = "files" if t == "stg_hpt__files" else t
        select = ", ".join(f"{expr} AS m{i}" for i, expr in enumerate(e.strip() for e in _split(aggs)))
        duck = con.sql(f"SELECT {select} FROM wh.main.{t}").fetchone()
        pg = con.sql(f"SELECT * FROM postgres_query('pg', 'SELECT {select} FROM published.{target}')").fetchone()
        match = len(duck) == len(pg) and all(_close(a, b) for a, b in zip(duck, pg))
        ok &= match
        print(f"[parity] {target}: {'OK' if match else 'MISMATCH'}  duckdb={duck}  postgres={pg}", flush=True)
    con.close()
    return ok

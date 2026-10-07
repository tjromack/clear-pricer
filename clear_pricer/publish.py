"""Publish the gated marts from DuckDB to a served Postgres, then prove parity (design pin 5).

Runs only after `dbt build` passed (the DAG wires it downstream of the gates), so a served store only ever holds a
gated build. Tables are written into `published_new` and swapped into `published` in one transaction: readers see
the old build or the new one, never a half-copied mix. After the swap, parity is checked by running the same
aggregates natively in both engines -- row counts, distinct keys, and sums of the money columns. Any mismatch fails
the task.

Two targets (CP-DEC 013):
- `local`    -- the Docker Postgres: every mart, including the 7.37M-row fact.
- `supabase` -- the hosted, public read tier: the small served set only (reports + the per-code price summary; the full
                detail ships as Parquet in a GitHub Release). Every table is locked down in the same transaction as
                the swap: row-level security on, one read-only policy, SELECT granted to Supabase's `anon` /
                `authenticated` roles, nothing else. The swap drops grants and policies, so they are re-applied
                every time; a reader can never catch an unlocked table.
"""

from __future__ import annotations

import math
from pathlib import Path

import duckdb

# The 9.86M-row NPPES history ships in the Parquet release only (CP-DEC 019): it is not served from a database.
SERVED = ("agg_code_prices", "rpt_npi_reconciliation", "rpt_npi_resolution", "rpt_npi_completeness",
          "rpt_source_conformance", "stg_hpt__files", "rpt_nppes_file_log", "dim_modifiers",
          "rpt_fhir_summary", "rpt_fhir_mapping", "rpt_fhir_code_bridge", "rpt_fhir_claim_totals")
TARGETS = {
    "local": ("fct_standard_charges", "dim_charge_codes") + SERVED,
    "supabase": SERVED,
}
PUBLIC_ROLES = ("anon", "authenticated")

# (table, aggregate SQL valid in both DuckDB and Postgres)
PARITY = {
    "fct_standard_charges": "count(*), count(distinct charge_id), count(distinct item_id), "
                            "sum(negotiated_rate), sum(gross_charge), sum(median_amount), count(negotiated_rate)",
    "dim_charge_codes": "count(*), count(distinct item_id), count(code_family)",
    "agg_code_prices": "count(*), sum(charge_rows), count(distinct code), sum(rate_median), sum(gross_median)",
    "dim_modifiers": "count(*)",
    "rpt_source_conformance": "count(*), sum(n)",
    "stg_hpt__files": "count(*), sum(records_read), sum(charge_rows)",
    "rpt_nppes_file_log": "count(*), sum(rows), sum(inserted), sum(updated), sum(deactivated), sum(unchanged)",
    "rpt_npi_resolution": "count(*), count(distinct npi), count(*) filter (where outcome like 'resolved_verified%')",
    "rpt_npi_completeness": "count(*), count(distinct npi), count(*) filter (where tier in (1, 2) and not disclosed), "
                            "sum(name_similarity)",
    "rpt_npi_reconciliation": "count(*), sum(disclosed_npis), sum(unresolved_npis), sum(undisclosed_candidates)",
    "rpt_fhir_summary": "count(*), sum(resources), sum(mapped_paths), sum(structural_issues)",
    "rpt_fhir_mapping": "count(*), sum(occurrences), count(*) filter (where mapped)",
    "rpt_fhir_code_bridge": "count(*), sum(claim_lines), sum(codes_with_a_price_file_code)",
    "rpt_fhir_claim_totals": "count(*), sum(claims), sum(header_cents), sum(lines_cents), sum(header_equals_lines)",
}


def served_name(table: str) -> str:
    return "files" if table == "stg_hpt__files" else table


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


def lockdown_sql(tables: tuple[str, ...]) -> str:
    """Grants + RLS for the public read roles, applied inside the swap transaction (Supabase only)."""
    roles = ", ".join(PUBLIC_ROLES)
    stmts = [f"GRANT USAGE ON SCHEMA published TO {roles};"]
    for t in (served_name(x) for x in tables):
        stmts += [f"ALTER TABLE published.{t} ENABLE ROW LEVEL SECURITY;",
                  f"CREATE POLICY public_read ON published.{t} FOR SELECT TO {roles} USING (true);",
                  f"GRANT SELECT ON published.{t} TO {roles};"]
    return " ".join(stmts)


def publish(warehouse: Path, dsn: str, target: str = "local") -> bool:
    """Every statement runs under `guard(dsn)`: driver errors echo the DSN, and the password must never be printed."""
    from clear_pricer.secrets import guard

    with guard(dsn):
        return _publish(warehouse, dsn, target)


def _publish(warehouse: Path, dsn: str, target: str) -> bool:
    tables = TARGETS[target]
    # in-memory session: the warehouse attached read-only, Postgres read-write (a read-only *connection* would make
    # every attachment read-only, Postgres included)
    con = duckdb.connect()
    con.sql("INSTALL postgres; LOAD postgres;")
    con.sql(f"ATTACH '{warehouse.as_posix()}' AS wh (READ_ONLY)")
    con.sql(f"ATTACH '{dsn}' AS pg (TYPE postgres)")
    con.sql("CALL postgres_execute('pg', 'DROP SCHEMA IF EXISTS published_new CASCADE; CREATE SCHEMA published_new;')")
    for t in tables:
        con.sql(f"CREATE TABLE pg.published_new.{served_name(t)} AS SELECT * FROM wh.main.{t}")
        print(f"[publish:{target}] {served_name(t)}: {con.sql(f'select count(*) from wh.main.{t}').fetchone()[0]:,} "
              "rows", flush=True)
    lockdown = lockdown_sql(tables) if target == "supabase" else ""
    swap = ("BEGIN; DROP SCHEMA IF EXISTS published CASCADE; ALTER SCHEMA published_new RENAME TO published; "
            f"{lockdown} COMMIT;")
    con.sql(f"CALL postgres_execute('pg', '{swap}')")

    ok = True
    for t in tables:
        aggs = PARITY[t]
        select = ", ".join(f"{expr} AS m{i}" for i, expr in enumerate(e.strip() for e in _split(aggs)))
        duck = con.sql(f"SELECT {select} FROM wh.main.{t}").fetchone()
        inner = f"SELECT {select} FROM published.{served_name(t)}".replace("'", "''")  # inside a SQL string literal
        pg = con.sql(f"SELECT * FROM postgres_query('pg', '{inner}')").fetchone()
        match = len(duck) == len(pg) and all(_close(a, b) for a, b in zip(duck, pg))
        ok &= match
        print(f"[parity:{target}] {served_name(t)}: {'OK' if match else 'MISMATCH'}  duckdb={duck}  postgres={pg}",
              flush=True)
    con.close()
    return ok

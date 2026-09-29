"""Read API over the published Parquet release (FastAPI + DuckDB). No database, no credentials (CP-DEC 013).

    uvicorn clear_pricer.api:app            # serves data/published/ (or $CLEAR_PRICER_PUBLISHED)

Read-only by construction: every query is a parameterised SELECT against Parquet files opened by an in-memory DuckDB
connection; there is nothing to write to. Numbers come from the same gated marts as the release and the served
Postgres, so the API can't disagree with them.
"""

from __future__ import annotations

import os
import threading
from decimal import Decimal
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Query

PUBLISHED = Path(os.environ.get("CLEAR_PRICER_PUBLISHED", Path(__file__).resolve().parents[1] / "data" / "published"))
RATE_BASES = ("dollar", "dollar_from_percent", "dollar_percent_unreconciled", "percent_only", "algorithm_only",
              "missing", "no_payer")

app = FastAPI(
    title="clear-pricer",
    description="Cleaned hospital price transparency data (Chicago v1) with its reconciliation failures published. "
                "Public data + synthetic FHIR only; no PHI.",
    version="0.1.0",
)
_local = threading.local()


def _con() -> duckdb.DuckDBPyConnection:
    """One in-memory DuckDB per worker thread, with each published Parquet file exposed as a view."""
    con = getattr(_local, "con", None)
    if con is None:
        if not (PUBLISHED / "manifest.json").exists():
            raise HTTPException(503, f"no published data at {PUBLISHED}; run `clear-pricer export` or fetch a release")
        con = duckdb.connect()
        for f in PUBLISHED.glob("*.parquet"):
            con.sql(f"CREATE VIEW {f.stem} AS SELECT * FROM read_parquet('{f.as_posix()}')")
        _local.con = con
    return con


def _rows(sql: str, params: list | None = None) -> list[dict]:
    cur = _con().execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [{c: _num(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def _num(v: object) -> object:
    """DECIMAL -> int (zero scale, e.g. summed counts) or float; JSON would otherwise carry numbers as strings."""
    if isinstance(v, Decimal):
        return int(v) if v.as_tuple().exponent >= 0 else float(v)
    return v


@app.get("/health")
def health() -> dict:
    import json

    manifest = json.loads((PUBLISHED / "manifest.json").read_text(encoding="utf-8"))
    return {"status": "ok", "fingerprint": manifest["fingerprint"],
            "files": {f["file"]: f["rows"] for f in manifest["files"]}}


@app.get("/hospitals")
def hospitals() -> list[dict]:
    """The v1 hospitals: source file, hash, template version, publish date, disclosed Type 2 NPIs."""
    return _rows("SELECT hospital_id, hospital_name, source_filename, source_sha256, template_version, "
                 "last_updated_on, type_2_npis, records_read, charge_rows FROM files ORDER BY hospital_id")


@app.get("/reconciliation")
def reconciliation() -> list[dict]:
    """NPI reconciliation headline: unresolved-NPI rate and disclosure coverage, per hospital and overall."""
    return _rows("SELECT * FROM rpt_npi_reconciliation ORDER BY hospital_id = 'ALL', hospital_id")


@app.get("/reconciliation/{hospital_id}")
def reconciliation_detail(hospital_id: str) -> dict:
    """The evidence: each disclosed NPI's resolution, and each undisclosed candidate with its tier and similarity."""
    res = _rows("SELECT * FROM rpt_npi_resolution WHERE hospital_id = ? ORDER BY npi", [hospital_id])
    if not res:
        raise HTTPException(404, f"unknown hospital {hospital_id!r}")
    cand = _rows("SELECT * FROM rpt_npi_completeness WHERE hospital_id = ? ORDER BY tier, npi", [hospital_id])
    return {"resolution": res, "completeness": cand}


@app.get("/conformance")
def conformance(hospital_id: str | None = None) -> list[dict]:
    """What each source file got wrong against the CMS v3 dictionary, and how often."""
    return _rows("SELECT * FROM rpt_source_conformance WHERE ? IS NULL OR hospital_id = ? ORDER BY hospital_id, n DESC",
                 [hospital_id, hospital_id])


@app.get("/prices/{code}")
def prices(code: str, hospital_id: str | None = None,
           rate_basis: str | None = Query(None, description=f"one of {', '.join(RATE_BASES)}")) -> list[dict]:
    """Per-hospital price summary for one billing code (CPT / HCPCS / CDT / MS-DRG), split by setting and rate basis.

    Filter `rate_basis=dollar` to compare contracted dollars only (CP-DEC 006)."""
    if rate_basis is not None and rate_basis not in RATE_BASES:
        raise HTTPException(422, f"rate_basis must be one of {RATE_BASES}")
    return _rows("SELECT * FROM agg_code_prices WHERE code = upper(?) AND (? IS NULL OR hospital_id = ?) "
                 "AND (? IS NULL OR rate_basis = ?) ORDER BY hospital_id, setting, rate_basis",
                 [code, hospital_id, hospital_id, rate_basis, rate_basis])


@app.get("/charges/{code}")
def charges(code: str, hospital_id: str | None = None, limit: int = Query(100, ge=1, le=1000),
            offset: int = Query(0, ge=0)) -> list[dict]:
    """Charge-level rows (payer x plan) for one billing code, from the full 7.37M-row fact."""
    return _rows(
        "SELECT f.hospital_id, f.charge_id, f.description, f.setting, f.payer_name, f.plan_name, f.methodology, "
        "f.negotiated_rate, f.rate_basis, f.negotiated_dollar, f.negotiated_percentage, f.gross_charge, "
        "f.discounted_cash, f.median_amount, f.allowed_amounts_suppressed "
        "FROM fct_standard_charges f "
        "WHERE f.item_id IN (SELECT item_id FROM dim_charge_codes WHERE code = upper(?)) "
        "AND (? IS NULL OR f.hospital_id = ?) ORDER BY f.hospital_id, f.charge_id LIMIT ? OFFSET ?",
        [code, hospital_id, hospital_id, limit, offset])

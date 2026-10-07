"""The release's grain contract and its check values (CP-DEC 020).

RELEASE_GRAIN names every table in the Parquet release and the key that identifies one row of it. The same keys are
gated in dbt (the `grain` test in the schema files; a pytest keeps the two lists identical) and documented in
docs/grain.md.

`check_values` computes a small set of numbers from the release tables: rows and distinct keys per table, the
charges -> codes fan-out, money checksums in integer cents, the NPPES history's interval invariants, and the
reconciliation headline. It runs three times over the same function:

1. at export, over the warehouse tables;
2. at export, over the Parquet just written -- 1 and 2 must match or the export fails (a type silently changed on the
   way to Parquet, the HUGEINT -> DOUBLE class of bug);
3. in `verify-release`, over a clean download -- it must match the published `check_values.json`.

So the release verifies itself from a clone, and the numbers double as a pinned answer key for anyone drilling on it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import duckdb

from clear_pricer.publish import served_name

REPO_SLUG = "tjromack/clear-pricer"

# warehouse table -> the key that identifies one row (docs/grain.md). Order = release order.
RELEASE_GRAIN: dict[str, tuple[str, ...]] = {
    "fct_standard_charges": ("charge_id",),
    "dim_charge_codes": ("item_id", "code_seq"),
    "agg_code_prices": ("hospital_id", "code_family", "code", "setting", "rate_basis"),
    "rpt_npi_reconciliation": ("hospital_id",),
    "rpt_npi_resolution": ("hospital_id", "npi"),
    "rpt_npi_completeness": ("hospital_id", "npi"),
    "rpt_source_conformance": ("hospital_id", "kind", "column"),
    "stg_hpt__files": ("hospital_id",),
    "rpt_nppes_file_log": ("seq",),
    "dim_modifiers": ("hospital_id", "code", "setting", "payer_name", "plan_name"),
    "rpt_fhir_summary": ("resource_type",),
    "rpt_fhir_mapping": ("resource_type", "path"),
    "rpt_fhir_code_bridge": ("code_system",),
    "rpt_fhir_claim_totals": ("claim_type",),
    "dim_provider_history": ("npi", "version"),
}

# the agg_code_prices code filter (dbt/models/marts/agg_code_prices.sql)
_AGG_CODES = ("code_family IN ('CPT_CAT_I', 'CPT_CAT_II', 'CPT_CAT_III', 'CPT_PLA', 'CPT_MAAA', 'HCPCS_II', 'CDT') "
              "OR declared_type = 'MS-DRG'")


def key_sql(table: str) -> str:
    return ", ".join(f'"{c}"' for c in RELEASE_GRAIN[table])


def warehouse_rel(table: str) -> str:
    return f"main.{table}"


def parquet_rel(directory: Path) -> Callable[[str], str]:
    return lambda table: f"read_parquet('{(directory / f'{served_name(table)}.parquet').as_posix()}')"


def _one(con: duckdb.DuckDBPyConnection, sql: str):
    return con.sql(sql).fetchone()


def check_values(con: duckdb.DuckDBPyConnection, rel: Callable[[str], str]) -> dict:
    """Every check value, computed over `rel(table)` (warehouse tables or Parquet files)."""
    tables = {}
    for t in RELEASE_GRAIN:
        rows, keys = _one(con, f"SELECT count(*), (SELECT count(*) FROM (SELECT DISTINCT {key_sql(t)} FROM {rel(t)})) "
                               f"FROM {rel(t)}")
        tables[served_name(t)] = {"key": list(RELEASE_GRAIN[t]), "rows": rows, "distinct_keys": keys}

    fct, codes, agg = rel("fct_standard_charges"), rel("dim_charge_codes"), rel("agg_code_prices")
    charges, x_codes = _one(con, f"SELECT (SELECT count(*) FROM {fct}), "
                                 f"(SELECT count(*) FROM {fct} f JOIN {codes} c USING (item_id))")
    multi_code_items = _one(con, f"SELECT count(*) FROM (SELECT item_id FROM {codes} GROUP BY 1 HAVING count(*) > 1)")[0]
    agg_sum, agg_hc, covered = _one(con, f"""SELECT
        (SELECT coalesce(sum(charge_rows), 0)::BIGINT FROM {agg}),
        (SELECT count(*) FROM (SELECT DISTINCT hospital_id, code FROM {agg})),
        (SELECT count(*) FROM {fct} WHERE item_id IN (SELECT item_id FROM {codes} WHERE {_AGG_CODES}))""")
    rate_cents, gross_cents = _one(con, f"SELECT coalesce(sum(round(negotiated_rate * 100)), 0)::BIGINT, "
                                        f"coalesce(sum(round(gross_charge * 100)), 0)::BIGINT FROM {fct}")
    hist = rel("dim_provider_history")
    npis, current, max_version, zero_len = _one(con, f"""SELECT count(DISTINCT npi), count(*) FILTER (WHERE is_current),
        coalesce(max(version), 0), count(*) FILTER (WHERE valid_to = valid_from) FROM {hist}""")
    overlaps = _one(con, f"""SELECT count(*) FROM {hist} a JOIN {hist} b
        ON a.npi = b.npi AND a.version < b.version
       AND a.valid_from < coalesce(b.valid_to, DATE '9999-12-31')
       AND b.valid_from < coalesce(a.valid_to, DATE '9999-12-31')""")[0]
    gaps = _one(con, f"""SELECT count(*) FROM (
        SELECT valid_to, lead(valid_from) OVER (PARTITION BY npi ORDER BY version) AS nxt FROM {hist})
        WHERE nxt IS NOT NULL AND valid_to IS DISTINCT FROM nxt""")[0]
    headline = _one(con, f"SELECT unresolved_rate::DOUBLE, disclosure_coverage::DOUBLE FROM {rel('rpt_npi_reconciliation')} "
                         "WHERE hospital_id = 'ALL'") or (None, None)

    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    derived = {
        "charges": charges,
        "charge_x_code_rows": x_codes,
        "charges_to_codes_fanout": ratio(x_codes, charges),
        "items_with_several_codes": multi_code_items,
        "agg_code_prices_charge_rows_sum": agg_sum,
        "agg_code_prices_charges_covered": covered,
        "agg_code_prices_fanout": ratio(agg_sum, covered),
        "agg_code_prices_hospital_code_pairs": agg_hc,
        "agg_code_prices_rows_per_hospital_code": ratio(tables["agg_code_prices"]["rows"], agg_hc),
        "negotiated_rate_sum_cents": rate_cents,
        "gross_charge_sum_cents": gross_cents,
        "provider_history_npis": npis,
        "provider_history_current_versions": current,
        "provider_history_max_version": max_version,
        "provider_history_zero_length_versions": zero_len,
        "provider_history_versions_valid_on_one_day": overlaps,
        "provider_history_gaps_between_versions": gaps,
        "npi_unresolved_rate_all": headline[0],
        "npi_disclosure_coverage_all": headline[1],
    }
    return {"tables": tables, "derived": derived}


def problems(values: dict) -> list[str]:
    """Invariants any release must satisfy, whatever its data."""
    out = [f"{name}: {v['rows']} rows but {v['distinct_keys']} distinct keys ({', '.join(v['key'])})"
           for name, v in values["tables"].items() if v["rows"] != v["distinct_keys"]]
    d = values["derived"]
    if d["provider_history_versions_valid_on_one_day"]:
        out.append(f"provider_history: {d['provider_history_versions_valid_on_one_day']} version pairs valid on one day")
    if d["provider_history_gaps_between_versions"]:
        out.append(f"provider_history: {d['provider_history_gaps_between_versions']} gaps between versions")
    if d["provider_history_current_versions"] != d["provider_history_npis"]:
        out.append("provider_history: current versions != NPIs")
    return out


def diff(expected: dict, actual: dict, path: str = "") -> list[str]:
    """Human-readable differences between two check-value documents."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        out = []
        for k in sorted(set(expected) | set(actual)):
            out += diff(expected.get(k), actual.get(k), f"{path}.{k}" if path else k)
        return out
    return [] if expected == actual else [f"{path}: published {expected!r}, recomputed {actual!r}"]


# ---------------------------------------------------------------------------------------------------- verify-release

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _curl(url: str, dest: Path | None = None) -> tuple[bool, bytes]:
    """curl, not Python HTTPS: it uses the OS trust store (CLAUDE.md, TLS-inspecting proxies)."""
    cmd = ["curl", "-fsSL", "--retry", "3", url] + (["-o", str(dest)] if dest else [])
    res = subprocess.run(cmd, capture_output=True, check=False)
    if res.returncode != 0 and dest is not None:
        dest.unlink(missing_ok=True)  # never leave a partial or empty file to be mistaken for the real one
    return res.returncode == 0, res.stdout


def resolve_tag(tag: str) -> str:
    if tag != "latest":
        return tag
    ok, body = _curl(f"https://api.github.com/repos/{REPO_SLUG}/releases/latest")
    if not ok:
        raise RuntimeError("could not resolve the latest release")
    return json.loads(body)["tag_name"]


def download(tag: str, dest: Path, log=print) -> None:
    base = f"https://github.com/{REPO_SLUG}/releases/download/{tag}"
    dest.mkdir(parents=True, exist_ok=True)
    if not _curl(f"{base}/manifest.json", dest / "manifest.json")[0]:
        raise RuntimeError(f"{tag}: no manifest.json")
    _curl(f"{base}/check_values.json", dest / "check_values.json")  # absent on releases cut before CP-DEC 020
    for f in json.loads((dest / "manifest.json").read_text(encoding="utf-8"))["files"]:
        log(f"[verify] downloading {f['file']} ({f['bytes'] / 1e6:.1f} MB)")
        if not _curl(f"{base}/{f['file']}", dest / f["file"])[0]:
            raise RuntimeError(f"{tag}: could not download {f['file']}")


def verify_dir(directory: Path, log=print) -> list[str]:
    """Verify a release directory: every file's hash against the manifest, then recompute the check values."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    failures = []
    for f in manifest["files"]:
        p = directory / f["file"]
        if not p.exists():
            failures.append(f"{f['file']}: missing")
        elif _sha256(p) != f["sha256"]:
            failures.append(f"{f['file']}: sha256 differs from manifest")
    log(f"[verify] {len(manifest['files'])} files hashed against the manifest: "
        f"{'OK' if not failures else f'{len(failures)} failed'}")
    cv_path = directory / "check_values.json"
    if not cv_path.exists():
        log("[verify] no check_values.json (a release cut before CP-DEC 020): hashes only")
        return failures
    published = json.loads(cv_path.read_text(encoding="utf-8"))
    if "check_values_sha256" in manifest and _sha256(cv_path) != manifest["check_values_sha256"]:
        failures.append("check_values.json: sha256 differs from manifest")
    con = duckdb.connect()
    con.sql("SET enable_progress_bar = false")
    try:
        recomputed = check_values(con, parquet_rel(directory))
    except duckdb.Error as e:  # a corrupt or missing file: a failed verification, not a crash
        failures.append(f"check values could not be recomputed: {str(e).splitlines()[0]}")
        log("[verify] check values could not be recomputed from the Parquet")
        return failures
    finally:
        con.close()
    d = diff(published, recomputed)
    failures += d + problems(recomputed)
    log(f"[verify] check values recomputed from the Parquet: {'match' if not d else f'{len(d)} differ'}")
    return failures


def verify_release(tag: str | None, directory: Path | None, keep: bool = False, log=print) -> int:
    """Exit code 0 when the release reproduces its own manifest and check values."""
    tmp = None
    if tag:
        tag = resolve_tag(tag)
        if directory is None:
            tmp = Path(tempfile.mkdtemp(prefix="cp-verify-"))
            directory = tmp
        log(f"[verify] {tag} -> {directory} (clean download)")
        download(tag, directory, log)
    assert directory is not None
    try:
        failures = verify_dir(directory, log)
    finally:
        if tmp and not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    for f in failures:
        log(f"[verify] FAIL {f}")
    log(f"[verify] {'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1

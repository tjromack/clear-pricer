"""Export the gated marts as the published Parquet release, and publish/fetch it as a GitHub Release (CP-DEC 013).

The release is the stranger's path to the full detail (design pin 5): no database, no account, no credentials --
download the Parquet (or read it straight over HTTPS) and query it with DuckDB.

Deterministic: rows are written in a total order with a single thread, so the same warehouse yields byte-identical
files. `manifest.json` pins every file (rows, bytes, SHA-256) and every input (price-file hashes, NPPES files applied,
Synthea version); its `fingerprint` hashes the inputs and the output files, and a new release is cut only when it
changes -- a daily hosted run with nothing new upstream (and no logic change) publishes nothing.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import duckdb

from clear_pricer.publish import SERVED, served_name

RELEASE_TABLES = ("fct_standard_charges", "dim_charge_codes") + SERVED
REPO_SLUG = "tjromack/clear-pricer"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inputs(con: duckdb.DuckDBPyConnection) -> dict:
    return {
        "price_files": [dict(zip(("hospital_id", "source_sha256", "last_updated_on"), r)) for r in con.sql(
            "SELECT hospital_id, source_sha256, last_updated_on FROM main.stg_hpt__files ORDER BY 1").fetchall()],
        "nppes_files": [r[0] for r in con.sql(
            "SELECT DISTINCT source_file FROM main.rpt_nppes_file_log WHERE outcome = 'applied' ORDER BY 1").fetchall()],
        "synthea": [dict(zip(("version", "jar_sha256", "args"), r)) for r in con.sql(
            "SELECT synthea_version, jar_sha256, args FROM main.stg_fhir__run").fetchall()],
    }


def export(warehouse: Path, out_dir: Path, log=lambda m: print(m, flush=True)) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(warehouse), read_only=True)
    con.sql("SET threads = 1")  # single-threaded writes: row groups (and so bytes) are deterministic
    con.sql("SET enable_progress_bar = false")
    files = []
    for t in RELEASE_TABLES:
        name = served_name(t)
        path = out_dir / f"{name}.parquet"
        con.sql(f"COPY (SELECT * FROM main.{t} ORDER BY ALL) TO '{path.as_posix()}' "
                "(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)")
        rows = con.sql(f"SELECT count(*) FROM main.{t}").fetchone()[0]
        files.append({"file": path.name, "rows": rows, "bytes": path.stat().st_size, "sha256": _sha256(path)})
        log(f"[export] {path.name}: {rows:,} rows, {path.stat().st_size / 1e6:.1f} MB")
    ins = inputs(con)
    con.close()
    # inputs AND output hashes: exports are byte-deterministic, so this changes exactly when the published data would
    # (new upstream files, or a logic change that moves a number) -- and never otherwise
    fingerprint = hashlib.sha256(json.dumps({"inputs": ins, "outputs": [f["sha256"] for f in files]},
                                            sort_keys=True).encode()).hexdigest()
    manifest = {"fingerprint": fingerprint, "inputs": ins, "files": files}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    return manifest


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def latest_release_fingerprint() -> str | None:
    """Fingerprint of the newest data release (from its notes), or None."""
    res = _gh("release", "list", "--repo", REPO_SLUG, "--limit", "20", "--json", "tagName", check=False)
    if res.returncode != 0:
        return None
    tags = [r["tagName"] for r in json.loads(res.stdout or "[]") if r["tagName"].startswith("data-")]
    if not tags:
        return None
    view = _gh("release", "view", tags[0], "--repo", REPO_SLUG, "--json", "body", check=False)
    body = json.loads(view.stdout or "{}").get("body", "") if view.returncode == 0 else ""
    marker = "fingerprint: `"
    return body.split(marker, 1)[1].split("`", 1)[0] if marker in body else None


def release(out_dir: Path, date: str, *, force: bool = False, log=lambda m: print(m, flush=True)) -> str | None:
    """Cut a GitHub Release of out_dir's Parquet + manifest -- only if the input fingerprint changed."""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    fp = manifest["fingerprint"]
    if not force and latest_release_fingerprint() == fp:
        log(f"[release] inputs unchanged (fingerprint {fp[:12]}) -- no new release")
        return None
    tag = f"data-{date}-{fp[:8]}"
    rows = "\n".join(f"| `{f['file']}` | {f['rows']:,} | {f['bytes'] / 1e6:.1f} MB | `{f['sha256'][:16]}` |"
                     for f in manifest["files"])
    notes = "\n".join([
        f"Published Parquet for clear-pricer, built {date} from the gated warehouse (every dbt gate passed).",
        "",
        f"fingerprint: `{fp}`",
        "",
        "| file | rows | size | sha256 (first 16) |",
        "|---|---|---|---|",
        rows,
        "",
        "Query without cloning: see `docs/QUERY.md`. Inputs (price-file hashes, NPPES files, Synthea version) are pinned "
        "in `manifest.json`. Public data + synthetic FHIR only; no PHI.",
    ])
    assets = [str(out_dir / f["file"]) for f in manifest["files"]] + [str(out_dir / "manifest.json")]
    _gh("release", "create", tag, "--repo", REPO_SLUG, "--title", f"Data release {date}", "--notes", notes, *assets)
    log(f"[release] created {tag} with {len(assets)} assets")
    return tag

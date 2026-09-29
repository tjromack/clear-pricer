"""Staging: parse a landed file and write deterministic Parquet for dbt.

Output: staging/hpt/<hospital>/{file,charges,codes,drift,quarantine}.parquet. The same landed file always yields
byte-identical Parquet (no timestamps, stable row order), so re-running a day changes nothing (design pin 2).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from clear_pricer import parse_csv_tall
from clear_pricer.fetch import LandedFile
from clear_pricer.sources import Hospital

S, F, I = pa.string(), pa.float64(), pa.int64()

CHARGES = pa.schema([
    ("charge_id", S), ("hospital_id", S), ("source_sha256", S), ("source_record", I),
    ("description", S), ("setting", S), ("billing_class", S), ("modifiers", S),
    ("drug_unit_of_measurement", F), ("drug_type_of_measurement", S),
    ("gross_charge", F), ("discounted_cash", F), ("payer_name", S), ("plan_name", S),
    ("negotiated_dollar", F), ("negotiated_percentage", F), ("negotiated_algorithm", S), ("methodology", S),
    ("negotiated_rate", F), ("rate_basis", S),
    ("count_raw", S), ("count_bucket", S), ("count_n", I),
    ("median_amount_raw", F), ("p10_amount_raw", F), ("p90_amount_raw", F),
    ("median_amount", F), ("p10_amount", F), ("p90_amount", F), ("allowed_amounts_suppressed", S),
    ("min_charge", F), ("max_charge", F), ("additional_generic_notes", S), ("unmapped_json", S),
])
CODES = pa.schema([
    ("charge_id", S), ("hospital_id", S), ("code_seq", I), ("code", S), ("declared_type", S),
    ("code_family", S), ("type_conflict", S),
])
DRIFT = pa.schema([
    ("hospital_id", S), ("source_sha256", S), ("kind", S), ("column", S), ("n", I),
    ("first_record", I), ("sample_value", S),
])
QUARANTINE = pa.schema([
    ("hospital_id", S), ("source_sha256", S), ("source_record", I), ("reason", S), ("raw_json", S),
])
FILE = pa.schema([
    ("hospital_id", S), ("source_sha256", S), ("source_filename", S), ("source_url", S), ("bytes", I),
    ("template_version", S), ("hospital_name", S), ("last_updated_on", S), ("location_names", pa.list_(S)),
    ("hospital_addresses", pa.list_(S)), ("type_2_npis", pa.list_(S)), ("license_state", S),
    ("license_number", S), ("attestation", S), ("records_read", I), ("charge_rows", I), ("quarantined_rows", I),
    ("unmapped_meta_json", S),
])


class StageError(RuntimeError):
    pass


def _write(rows: list[dict], schema: pa.Schema, path: Path) -> None:
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path, compression="zstd")


def stage(h: Hospital, landed: LandedFile, data_dir: Path) -> dict:
    if h.format != "csv_tall":
        raise StageError(f"{h.id}: parser for format {h.format!r} lands in a later milestone")

    path = Path(landed.path)
    with path.open("rb") as f:
        had_bom = f.read(3) == b"\xef\xbb\xbf"
    with path.open(encoding="utf-8-sig", newline="") as f:
        result = parse_csv_tall.parse(f)

    for fix in landed.fixes:
        result.drift.add(f"discovery_{fix}", "cms-hpt.txt")
    if had_bom:
        result.drift.add("utf8_bom_stripped")

    sha, hid = landed.sha256, h.id

    def cid(record: int) -> str:
        return f"{hid}-{sha[:16]}-{record}"

    charges = [{"charge_id": cid(r["source_record"]), "hospital_id": hid, "source_sha256": sha} | r
               for r in result.charges]
    codes = [{"charge_id": cid(c.pop("source_record")), "hospital_id": hid} | c for c in result.codes]
    drift = [{"hospital_id": hid, "source_sha256": sha} | d for d in result.drift.rows()]
    quarantine = [{"hospital_id": hid, "source_sha256": sha} | q for q in result.quarantine]
    m = result.meta
    file_row = {
        "hospital_id": hid, "source_sha256": sha, "source_filename": landed.filename, "source_url": landed.source_url,
        "bytes": landed.bytes, "template_version": m.get("version"), "hospital_name": m.get("hospital_name"),
        "last_updated_on": m.get("last_updated_on"), "location_names": m.get("location_name", []),
        "hospital_addresses": m.get("hospital_address", []), "type_2_npis": m.get("type_2_npi", []),
        "license_state": m.get("license_state"), "license_number": m.get("license_number"),
        "attestation": m.get("attestation"), "records_read": result.records_read,
        "charge_rows": len(charges), "quarantined_rows": len(quarantine),
        "unmapped_meta_json": json.dumps(m.get("unmapped", {}), sort_keys=True) if m.get("unmapped") else None,
    }

    out = data_dir / "staging" / "hpt" / hid
    if out.exists():
        shutil.rmtree(out)  # one current staged file per hospital; history lives in raw/ by content hash
    out.mkdir(parents=True)
    _write(charges, CHARGES, out / "charges.parquet")
    _write(codes, CODES, out / "codes.parquet")
    _write(drift, DRIFT, out / "drift.parquet")
    _write(quarantine, QUARANTINE, out / "quarantine.parquet")
    _write([file_row], FILE, out / "file.parquet")
    return {"records_read": result.records_read, "charge_rows": len(charges), "code_rows": len(codes),
            "quarantined_rows": len(quarantine), "drift_kinds": len(drift)}

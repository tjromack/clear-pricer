"""Staging: parse a landed file and stream deterministic Parquet for dbt.

Output: staging/hpt/<hospital>/{file,charges,codes,modifiers,drift,quarantine}.parquet. Rows are written in fixed-size
batches (bounded memory for the 5 GB NM file), in source order, with no timestamps -- so the same landed file always
yields byte-identical Parquet and re-running a day changes nothing (design pin 2).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from clear_pricer.fetch import LandedFile
from clear_pricer.parse_csv_tall import CsvTallParse
from clear_pricer.parse_json import JsonParse
from clear_pricer.sources import Hospital

S, F, I = pa.string(), pa.float64(), pa.int64()
BATCH = 100_000

CHARGES = pa.schema([
    ("charge_id", S), ("item_id", S), ("hospital_id", S), ("source_sha256", S), ("source_locator", S),
    ("description", S), ("setting", S), ("billing_class", S), ("modifiers", S),
    ("drug_unit_of_measurement", F), ("drug_type_of_measurement", S),
    ("gross_charge", F), ("discounted_cash", F), ("payer_name", S), ("plan_name", S),
    ("negotiated_dollar", F), ("negotiated_percentage", F), ("negotiated_algorithm", S), ("methodology", S),
    ("negotiated_rate", F), ("rate_basis", S),
    ("count_raw", S), ("count_bucket", S), ("count_n", I),
    ("median_amount_raw", F), ("p10_amount_raw", F), ("p90_amount_raw", F),
    ("median_amount", F), ("p10_amount", F), ("p90_amount", F), ("allowed_amounts_suppressed", S),
    ("min_charge", F), ("max_charge", F), ("additional_generic_notes", S), ("additional_payer_notes", S),
    ("unmapped_json", S),
])
CODES = pa.schema([
    ("item_id", S), ("hospital_id", S), ("code_seq", I), ("code", S), ("declared_type", S),
    ("code_family", S), ("type_conflict", S),
])
MODIFIERS = pa.schema([
    ("hospital_id", S), ("source_sha256", S), ("modifier_locator", S), ("code", S), ("description", S),
    ("setting", S), ("payer_name", S), ("plan_name", S), ("payer_description", S),
])
DRIFT = pa.schema([
    ("hospital_id", S), ("source_sha256", S), ("kind", S), ("column", S), ("n", I),
    ("first_locator", S), ("sample_value", S),
])
QUARANTINE = pa.schema([
    ("hospital_id", S), ("source_sha256", S), ("source_locator", S), ("reason", S), ("raw_json", S),
])
FILE = pa.schema([
    ("hospital_id", S), ("source_sha256", S), ("source_filename", S), ("source_url", S), ("bytes", I),
    ("source_format", S), ("template_version", S), ("hospital_name", S), ("last_updated_on", S),
    ("location_names", pa.list_(S)), ("hospital_addresses", pa.list_(S)), ("type_2_npis", pa.list_(S)),
    ("license_state", S), ("license_number", S), ("attestation", S), ("records_read", I), ("charge_rows", I),
    ("quarantined_rows", I), ("unmapped_meta_json", S),
])


class StageError(RuntimeError):
    pass


class _Sink:
    """Buffered, deterministic Parquet writer (always writes a file, even with zero rows)."""

    def __init__(self, path: Path, schema: pa.Schema):
        self.schema, self.rows, self.count = schema, [], 0
        self.writer = pq.ParquetWriter(path, schema, compression="zstd")

    def add(self, row: dict) -> None:
        self.rows.append(row)
        self.count += 1
        if len(self.rows) >= BATCH:
            self.flush()

    def flush(self) -> None:
        if self.rows:
            self.writer.write_table(pa.Table.from_pylist(self.rows, schema=self.schema))
            self.rows = []

    def close(self) -> None:
        self.flush()
        self.writer.close()


def stage(h: Hospital, landed: LandedFile, data_dir: Path) -> dict:
    path = Path(landed.path)
    with path.open("rb") as f:
        had_bom = f.read(3) == b"\xef\xbb\xbf"

    final = data_dir / "staging" / "hpt" / h.id
    out = data_dir / "staging" / ".partial" / h.id  # outside the dbt glob; swapped in after a complete parse
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    sinks = {name: _Sink(out / f"{name}.parquet", schema) for name, schema in
             (("charges", CHARGES), ("codes", CODES), ("modifiers", MODIFIERS), ("quarantine", QUARANTINE))}

    sha, hid = landed.sha256, h.id
    prefix = f"{hid}-{sha[:16]}-"
    base = {"hospital_id": hid, "source_sha256": sha}

    if h.format == "csv_tall":
        fh = path.open(encoding="utf-8-sig", newline="")
        parse = CsvTallParse(fh)
    elif h.format == "json":
        fh = path.open("rb")
        fh.seek(3 if had_bom else 0)
        parse = JsonParse(fh)
    else:
        raise StageError(f"{h.id}: no parser for format {h.format!r}")

    try:
        for kind, row in parse.events():
            if kind == "charge":
                row["charge_id"] = prefix + row.pop("source_locator")
                row["item_id"] = prefix + row.pop("item_locator")
                row["source_locator"] = row["charge_id"][len(prefix):]
                sinks["charges"].add(base | row)
            elif kind == "code":
                row["item_id"] = prefix + row.pop("item_locator")
                sinks["codes"].add({"hospital_id": hid} | row)
            elif kind == "modifier":
                sinks["modifiers"].add(base | row)
            elif kind == "quarantine":
                sinks["quarantine"].add(base | row)
    finally:
        fh.close()
        for s in sinks.values():
            s.close()

    drift = parse.drift
    for fix in landed.fixes:
        drift.add(f"discovery_{fix}", "cms-hpt.txt")
    if had_bom:
        drift.add("utf8_bom_stripped")
    pq.write_table(pa.Table.from_pylist([base | d for d in drift.rows()], schema=DRIFT), out / "drift.parquet",
                   compression="zstd")

    m = parse.meta
    file_row = base | {
        "source_filename": landed.filename, "source_url": landed.source_url, "bytes": landed.bytes,
        "source_format": h.format, "template_version": m.version, "hospital_name": m.hospital_name,
        "last_updated_on": m.last_updated_on, "location_names": m.location_name,
        "hospital_addresses": m.hospital_address, "type_2_npis": m.type_2_npi, "license_state": m.license_state,
        "license_number": m.license_number, "attestation": m.attestation, "records_read": parse.records_read,
        "charge_rows": sinks["charges"].count, "quarantined_rows": sinks["quarantine"].count,
        "unmapped_meta_json": json.dumps(m.unmapped, sort_keys=True, default=str) if m.unmapped else None,
    }
    pq.write_table(pa.Table.from_pylist([file_row], schema=FILE), out / "file.parquet", compression="zstd")
    if final.exists():
        shutil.rmtree(final)  # one current staged file per hospital; history lives in raw/ by content hash
    final.parent.mkdir(parents=True, exist_ok=True)
    out.rename(final)
    return {"records_read": parse.records_read, "charge_rows": sinks["charges"].count,
            "code_rows": sinks["codes"].count, "modifier_rows": sinks["modifiers"].count,
            "quarantined_rows": sinks["quarantine"].count, "drift_kinds": len(drift.entries)}

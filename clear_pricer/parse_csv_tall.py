"""Parser for CMS HPT v3 CSV "tall" machine-readable files.

Pure: takes an iterable of text lines (no file or network I/O), yields normalised charge + code rows, and keeps a
drift log of everything it could not map (design pin 3: record, never silently drop). Nothing is discarded:
- columns outside the v3 dictionary are kept per row in `unmapped_json` and logged as `unmapped_column`;
- values that fail their type/enum are nulled in the clean column, kept raw where a *_raw column exists, and logged;
- rows whose width does not match the header are quarantined (raw cells kept) and logged as `ragged_row`.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from clear_pricer import rules

ATTESTATION_PREFIX = "to the best of its knowledge and belief"

META_KNOWN = {"hospital_name", "last_updated_on", "version", "location_name", "hospital_address", "type_2_npi",
              "attester_name", "financial_aid_policy", "general_contract_provisions"}

# row-3 headers, v3.0 CSV tall (code|[i] and code|[i]|type handled separately)
DATA_REQUIRED = (
    "description", "setting", "modifiers", "drug_unit_of_measurement", "drug_type_of_measurement",
    "standard_charge|gross", "standard_charge|discounted_cash", "payer_name", "plan_name",
    "standard_charge|negotiated_dollar", "standard_charge|negotiated_percentage",
    "standard_charge|negotiated_algorithm", "median_amount", "10th_percentile", "90th_percentile", "count",
    "standard_charge|methodology", "standard_charge|min", "standard_charge|max", "additional_generic_notes",
)
DATA_OPTIONAL = ("billing_class",)
_CODE_COL = re.compile(r"code\|(\d+)")
_CODE_TYPE_COL = re.compile(r"code\|(\d+)\|type")


def norm_header(h: str) -> str:
    """Spec: headers are case-insensitive and spaces around pipes are tolerated."""
    return "|".join(part.strip() for part in h.strip().lower().split("|"))


def split_multi(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split("|") if v.strip()]


@dataclass
class DriftLog:
    """Aggregated parser observations: (kind, column) -> count, first record, first sample value."""
    entries: dict[tuple[str, str], dict] = field(default_factory=dict)

    def add(self, kind: str, column: str = "", sample: str | None = None, record: int | None = None) -> None:
        e = self.entries.get((kind, column))
        if e is None:
            self.entries[(kind, column)] = {"kind": kind, "column": column, "n": 1, "first_record": record,
                                            "sample_value": None if sample is None else str(sample)[:200]}
        else:
            e["n"] += 1

    def rows(self) -> list[dict]:
        return sorted(self.entries.values(), key=lambda e: (e["kind"], e["column"]))


@dataclass
class ParseResult:
    meta: dict
    charges: list[dict]
    codes: list[dict]
    quarantine: list[dict]
    drift: DriftLog
    records_read: int


def parse(lines: Iterable[str]) -> ParseResult:
    drift = DriftLog()
    reader = csv.reader(lines)
    try:
        meta_header = [norm_header(h) for h in next(reader)]
        meta_values = next(reader)
        header_raw = next(reader)
    except StopIteration:
        drift.add("truncated_header")
        return ParseResult({}, [], [], [], drift, 0)

    meta = _parse_meta(meta_header, meta_values, drift)
    header = [norm_header(h) for h in header_raw]
    index = _map_header(header, drift)

    charges: list[dict] = []
    codes: list[dict] = []
    quarantine: list[dict] = []
    records = 0
    for record_no, cells in enumerate(reader, start=4):  # file records 1-3 are headers
        if not any(c.strip() for c in cells):
            drift.add("blank_row", record=record_no)
            continue
        records += 1
        if len(cells) != len(header):
            drift.add("ragged_row", sample=f"{len(cells)} cells vs {len(header)}", record=record_no)
            quarantine.append({"source_record": record_no, "reason": "ragged_row",
                               "raw_json": json.dumps(cells, ensure_ascii=False)})
            continue
        charges.append(_parse_row(record_no, cells, index, drift))
        codes.extend(_parse_codes(record_no, cells, index, drift))
    return ParseResult(meta, charges, codes, quarantine, drift, records)


def _parse_meta(header: list[str], values: list[str], drift: DriftLog) -> dict:
    meta: dict = {"license_state": None, "license_number": None, "attestation": None, "unmapped": {}}
    for h, v in zip(header, values + [""] * (len(header) - len(values))):
        v = v.strip()
        if not h:
            if v:
                drift.add("meta_value_without_header", sample=v)
            continue
        if h.startswith(ATTESTATION_PREFIX):
            meta["attestation"] = v.lower() if v else None
        elif h.startswith("license_number"):
            meta["license_state"] = h.split("|", 1)[1].upper() if "|" in h else None
            meta["license_number"] = v or None
        elif h in META_KNOWN:
            meta[h] = v or None
        else:
            meta["unmapped"][h] = v
            drift.add("unmapped_meta_column", h, v)
    for key in ("location_name", "hospital_address", "type_2_npi"):
        meta[key] = split_multi(meta.get(key))
    for req in ("hospital_name", "last_updated_on", "version"):
        if not meta.get(req):
            drift.add("missing_required_meta", req)
    if meta["attestation"] is None:
        drift.add("missing_required_meta", "attestation")
    return meta


def _map_header(header: list[str], drift: DriftLog) -> dict:
    seen: dict[str, int] = {}
    code_cols: dict[int, dict[str, int]] = {}
    unmapped: dict[str, int] = {}
    for i, h in enumerate(header):
        if h in seen:
            drift.add("duplicate_column", h)
            continue
        seen[h] = i
        if m := _CODE_TYPE_COL.fullmatch(h):
            code_cols.setdefault(int(m.group(1)), {})["type"] = i
        elif m := _CODE_COL.fullmatch(h):
            code_cols.setdefault(int(m.group(1)), {})["code"] = i
        elif h not in DATA_REQUIRED and h not in DATA_OPTIONAL:
            unmapped[h] = i
            drift.add("unmapped_column", h)
    for req in DATA_REQUIRED:
        if req not in seen:
            drift.add("missing_required_column", req)
    if 1 not in code_cols:
        drift.add("missing_required_column", "code|1")
    return {"cols": seen, "codes": code_cols, "unmapped": unmapped}


def _get(cells: list[str], index: dict, col: str) -> str | None:
    i = index["cols"].get(col)
    if i is None:
        return None
    v = cells[i].strip()
    return v or None


def _num(cells, index, col, drift, record) -> float | None:
    raw = _get(cells, index, col)
    value, ok = rules.parse_number(raw)
    if not ok:
        drift.add("unparseable_numeric", col, raw, record)
    elif value is not None and value <= 0:
        drift.add("nonpositive_numeric", col, raw, record)  # spec: numerics must be positive; kept, logged
    return value


def _enum(cells, index, col, valid, drift, record) -> str | None:
    raw = _get(cells, index, col)
    value, ok = rules.norm_enum(raw, valid)
    if not ok:
        drift.add("invalid_enum", col, raw, record)
        return None
    return value


def _parse_row(record: int, cells: list[str], index: dict, drift: DriftLog) -> dict:
    g = lambda col: _get(cells, index, col)  # noqa: E731
    n = lambda col: _num(cells, index, col, drift, record)  # noqa: E731

    gross = n("standard_charge|gross")
    dollar = n("standard_charge|negotiated_dollar")
    pct = n("standard_charge|negotiated_percentage")
    median_raw, p10_raw, p90_raw = n("median_amount"), n("10th_percentile"), n("90th_percentile")
    count_raw = g("count")
    count = rules.parse_count(count_raw)
    if count.bucket == "invalid":
        drift.add("invalid_count", "count", count_raw, record)
    allowed = rules.clean_allowed_amounts(count, median_raw, p10_raw, p90_raw)
    if allowed.suppressed_reason:
        drift.add("allowed_amounts_with_zero_count", "median_amount", median_raw, record)
    algorithm = g("standard_charge|negotiated_algorithm")
    rate = rules.resolve_rate(g("payer_name"), dollar, pct, algorithm, gross)
    if rate.rate_basis in ("dollar_percent_unreconciled", "missing"):
        drift.add(f"rate_{rate.rate_basis}", "standard_charge|negotiated_dollar",
                  f"dollar={dollar} pct={pct} gross={gross}", record)

    unmapped = {h: cells[i] for h, i in index["unmapped"].items() if cells[i].strip()}
    return {
        "source_record": record,
        "description": g("description"),
        "setting": _enum(cells, index, "setting", rules.SETTINGS, drift, record),
        "billing_class": _enum(cells, index, "billing_class", rules.BILLING_CLASSES, drift, record),
        "modifiers": g("modifiers"),
        "drug_unit_of_measurement": n("drug_unit_of_measurement"),
        "drug_type_of_measurement": g("drug_type_of_measurement"),
        "gross_charge": gross,
        "discounted_cash": n("standard_charge|discounted_cash"),
        "payer_name": g("payer_name"),
        "plan_name": g("plan_name"),
        "negotiated_dollar": dollar,
        "negotiated_percentage": pct,
        "negotiated_algorithm": algorithm,
        "methodology": _enum(cells, index, "standard_charge|methodology", rules.METHODOLOGIES, drift, record),
        "negotiated_rate": rate.negotiated_rate,
        "rate_basis": rate.rate_basis,
        "count_raw": count_raw,
        "count_bucket": count.bucket,
        "count_n": count.n,
        "median_amount_raw": median_raw,
        "p10_amount_raw": p10_raw,
        "p90_amount_raw": p90_raw,
        "median_amount": allowed.median,
        "p10_amount": allowed.p10,
        "p90_amount": allowed.p90,
        "allowed_amounts_suppressed": allowed.suppressed_reason,
        "min_charge": n("standard_charge|min"),
        "max_charge": n("standard_charge|max"),
        "additional_generic_notes": g("additional_generic_notes"),
        "unmapped_json": json.dumps(unmapped, ensure_ascii=False, sort_keys=True) if unmapped else None,
    }


def _parse_codes(record: int, cells: list[str], index: dict, drift: DriftLog) -> Iterator[dict]:
    for seq in sorted(index["codes"]):
        cols = index["codes"][seq]
        code = cells[cols["code"]].strip() if "code" in cols else ""
        ctype = cells[cols["type"]].strip() if "type" in cols else ""
        if not code and not ctype:
            continue
        if not code or not ctype:  # spec conditional #3
            drift.add("code_type_pair_incomplete", f"code|{seq}", f"code={code!r} type={ctype!r}", record)
            if not code:
                continue
        cls = rules.classify_code(code, ctype or None)
        if cls.type_conflict:
            drift.add(f"code_{cls.type_conflict}", f"code|{seq}|type", f"{cls.code} as {cls.declared_type}", record)
        yield {"source_record": record, "code_seq": seq, "code": cls.code, "declared_type": cls.declared_type,
               "code_family": cls.code_family, "type_conflict": cls.type_conflict}

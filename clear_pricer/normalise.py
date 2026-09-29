"""Format-independent row building shared by the CSV and JSON parsers.

Parsers extract raw values (already type-coerced, with coercion failures logged) and call these builders, so the
CP-DEC 006 rules are applied identically whatever the source format. Pure: no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field

from clear_pricer import rules


@dataclass
class DriftLog:
    """Aggregated parser observations: (kind, column) -> count, first locator, first sample value."""
    entries: dict[tuple[str, str], dict] = field(default_factory=dict)

    def add(self, kind: str, column: str = "", sample: object = None, locator: str | None = None) -> None:
        e = self.entries.get((kind, column))
        if e is None:
            self.entries[(kind, column)] = {"kind": kind, "column": column, "n": 1, "first_locator": locator,
                                            "sample_value": None if sample is None else str(sample)[:200]}
        else:
            e["n"] += 1

    def rows(self) -> list[dict]:
        return sorted(self.entries.values(), key=lambda e: (e["kind"], e["column"]))


def enum(value: str | None, valid: frozenset[str], column: str, drift: DriftLog, locator: str) -> str | None:
    v, ok = rules.norm_enum(value, valid)
    if not ok:
        drift.add("invalid_enum", column, value, locator)
        return None
    return v


def check_positive(value: float | None, column: str, drift: DriftLog, locator: str) -> float | None:
    if value is not None and value <= 0:
        drift.add("nonpositive_numeric", column, value, locator)  # spec: numerics must be positive; kept, logged
    return value


def charge_row(
    *, locator: str, item_locator: str, description: str | None, setting: str | None, billing_class: str | None,
    modifiers: str | None, drug_unit: float | None, drug_type: str | None, gross: float | None,
    cash: float | None, payer_name: str | None, plan_name: str | None, dollar: float | None,
    percentage: float | None, algorithm: str | None, methodology: str | None, median: float | None,
    p10: float | None, p90: float | None, count_raw: str | None, minimum: float | None, maximum: float | None,
    generic_notes: str | None, payer_notes: str | None, unmapped: dict, drift: DriftLog,
) -> dict:
    count = rules.parse_count(count_raw)
    if count.bucket == "invalid":
        drift.add("invalid_count", "count", count_raw, locator)
    allowed = rules.clean_allowed_amounts(count, median, p10, p90)
    if allowed.suppressed_reason:
        drift.add("allowed_amounts_with_zero_count", "median_amount", median, locator)
    rate = rules.resolve_rate(payer_name, dollar, percentage, algorithm, gross)
    if rate.rate_basis in ("dollar_percent_unreconciled", "missing"):
        drift.add(f"rate_{rate.rate_basis}", "negotiated_dollar",
                  f"dollar={dollar} pct={percentage} gross={gross}", locator)
    return {
        "source_locator": locator,
        "item_locator": item_locator,
        "description": description,
        "setting": enum(setting, rules.SETTINGS, "setting", drift, locator),
        "billing_class": enum(billing_class, rules.BILLING_CLASSES, "billing_class", drift, locator),
        "modifiers": modifiers,
        "drug_unit_of_measurement": drug_unit,
        "drug_type_of_measurement": drug_type,
        "gross_charge": gross,
        "discounted_cash": cash,
        "payer_name": payer_name,
        "plan_name": plan_name,
        "negotiated_dollar": dollar,
        "negotiated_percentage": percentage,
        "negotiated_algorithm": algorithm,
        "methodology": enum(methodology, rules.METHODOLOGIES, "methodology", drift, locator),
        "negotiated_rate": rate.negotiated_rate,
        "rate_basis": rate.rate_basis,
        "count_raw": count_raw,
        "count_bucket": count.bucket,
        "count_n": count.n,
        "median_amount_raw": median,
        "p10_amount_raw": p10,
        "p90_amount_raw": p90,
        "median_amount": allowed.median,
        "p10_amount": allowed.p10,
        "p90_amount": allowed.p90,
        "allowed_amounts_suppressed": allowed.suppressed_reason,
        "min_charge": minimum,
        "max_charge": maximum,
        "additional_generic_notes": generic_notes,
        "additional_payer_notes": payer_notes,
        "unmapped_json": json.dumps(unmapped, ensure_ascii=False, sort_keys=True, default=str) if unmapped else None,
    }


def code_rows(item_locator: str, pairs: list[tuple[int, str, str]], drift: DriftLog) -> Iterator[dict]:
    """pairs: (seq, code, declared_type) with blanks as ''. Spec conditional #3: code and type come together."""
    for seq, code, ctype in pairs:
        code, ctype = (code or "").strip(), (ctype or "").strip()
        if not code and not ctype:
            continue
        if not code or not ctype:
            drift.add("code_type_pair_incomplete", f"code|{seq}", f"code={code!r} type={ctype!r}", item_locator)
            if not code:
                continue
        cls = rules.classify_code(code, ctype or None)
        if cls.type_conflict:
            drift.add(f"code_{cls.type_conflict}", "code_type", f"{cls.code} as {cls.declared_type}", item_locator)
        yield {"item_locator": item_locator, "code_seq": seq, "code": cls.code, "declared_type": cls.declared_type,
               "code_family": cls.code_family, "type_conflict": cls.type_conflict}


@dataclass
class Meta:
    """File-level header fields, common to both formats."""
    hospital_name: str | None = None
    last_updated_on: str | None = None
    version: str | None = None
    location_name: list[str] = field(default_factory=list)
    hospital_address: list[str] = field(default_factory=list)
    type_2_npi: list[str] = field(default_factory=list)
    license_state: str | None = None
    license_number: str | None = None
    attestation: str | None = None
    attester_name: str | None = None
    unmapped: dict = field(default_factory=dict)

    def check_required(self, drift: DriftLog) -> None:
        for name in ("hospital_name", "last_updated_on", "version", "attestation"):
            if not getattr(self, name):
                drift.add("missing_required_meta", name)
        for name in ("location_name", "hospital_address", "type_2_npi"):
            if not getattr(self, name):
                drift.add("missing_required_meta", name)

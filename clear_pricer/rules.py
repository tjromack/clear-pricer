"""Pure normalisation rules for CMS Hospital Price Transparency data (CP-DEC 006).

No I/O. Every function here is total over its inputs and unit-tested in tests/test_rules.py.
Source: CMS HPT CSV Data Dictionary v3.0 (github.com/CMSgov/hospital-price-transparency).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- enums (spec: valid values are case-insensitive) -------------------------------------------

CODE_TYPES = frozenset({
    "CPT", "NDC", "HCPCS", "RC", "ICD", "DRG", "MS-DRG", "R-DRG", "S-DRG", "APS-DRG", "AP-DRG",
    "APR-DRG", "APC", "LOCAL", "EAPG", "HIPPS", "CDT", "CDM", "TRIS-DRG", "CMG", "MS-LTC-DRG",
})
SETTINGS = frozenset({"inpatient", "outpatient", "both"})
METHODOLOGIES = frozenset({"case rate", "fee schedule", "percent of total billed charges", "per diem", "other"})
BILLING_CLASSES = frozenset({"professional", "facility", "both"})


def norm_enum(value: str | None, valid: frozenset[str], *, upper: bool = False) -> tuple[str | None, bool]:
    """Trim + case-fold an enum value. Returns (normalised value or None, is_valid). Blank -> (None, True)."""
    if value is None or not value.strip():
        return None, True
    v = value.strip()
    v = v.upper() if upper else v.lower()
    return v, v in valid


# --- numbers ----------------------------------------------------------------------------------

def parse_number(value: str | None) -> tuple[float | None, bool]:
    """Spec numerics are bare numbers (no '$', no thousands separator). Returns (value, ok). Blank -> (None, True)."""
    if value is None or not value.strip():
        return None, True
    try:
        return float(value.strip()), True
    except ValueError:
        return None, False


# --- count of allowed amounts -----------------------------------------------------------------

@dataclass(frozen=True)
class Count:
    bucket: str | None  # '0' | '1-10' | '11+' | None (blank) | 'invalid'
    n: int | None       # exact count when the spec gives one (0, or >= 11)


def parse_count(value: str | None) -> Count:
    """Spec: allowed values are "0", "1 through 10", and whole numbers >= 11 without separators."""
    if value is None or not value.strip():
        return Count(None, None)
    v = value.strip().lower()
    if v == "0":
        return Count("0", 0)
    if v == "1 through 10":
        return Count("1-10", None)
    if v.isdigit() and int(v) >= 11:
        return Count("11+", int(v))
    return Count("invalid", None)


# --- allowed amounts: never publish a median of zero claims -----------------------------------

@dataclass(frozen=True)
class AllowedAmounts:
    median: float | None
    p10: float | None
    p90: float | None
    suppressed_reason: str | None  # 'count_zero' when the source encoded amounts it must not have


def clean_allowed_amounts(count: Count, median: float | None, p10: float | None, p90: float | None) -> AllowedAmounts:
    """Spec: "If the count of allowed amounts is zero, do not encode these data elements."

    A median/percentile encoded alongside count 0 describes zero remittances, so it is nulled (not zeroed:
    0 would assert a $0 payment). The raw values stay in the *_raw columns; this only governs the clean ones.
    """
    if count.bucket == "0" and any(v is not None for v in (median, p10, p90)):
        return AllowedAmounts(None, None, None, "count_zero")
    return AllowedAmounts(median, p10, p90, None)


# --- negotiated rate precedence ----------------------------------------------------------------

RATE_BASES = frozenset({
    "dollar",                       # dollar only: the contracted dollar amount
    "dollar_from_percent",          # dollar + percentage, and dollar == pct/100 * gross to within 1 cent
    "dollar_percent_unreconciled",  # dollar + percentage that do not reconcile against gross
    "percent_only",                 # percentage, no dollar: no dollar price is claimed
    "algorithm_only",               # algorithm text, no dollar or percentage
    "missing",                      # payer named but no dollar / percentage / algorithm (spec conditional #1)
    "no_payer",                     # gross / cash-only row
})


@dataclass(frozen=True)
class Rate:
    negotiated_rate: float | None
    rate_basis: str


def resolve_rate(
    payer_name: str | None,
    dollar: float | None,
    percentage: float | None,
    algorithm: str | None,
    gross: float | None,
) -> Rate:
    """The dollar amount is the price whenever it is present (spec: "if a dollar can be calculated, calculate
    it and encode it"). rate_basis records where that dollar came from, so a comparison can exclude dollars
    that are just a percentage applied to the chargemaster price.
    """
    has_payer = bool(payer_name and payer_name.strip())
    has_algo = bool(algorithm and algorithm.strip())
    if dollar is not None:
        if percentage is None:
            return Rate(dollar, "dollar")
        # within one cent of the exact product, in cents: publishers both round (1.3054 -> 1.31) and truncate
        # (-> 1.30). Comparing rounded floats instead mis-flags truncation, since 1.31 - 1.30 > 0.01 in float.
        if gross is not None and abs(percentage * gross - dollar * 100) < 1.0:
            return Rate(dollar, "dollar_from_percent")
        return Rate(dollar, "dollar_percent_unreconciled")
    if percentage is not None:
        return Rate(None, "percent_only")
    if has_algo:
        return Rate(None, "algorithm_only")
    return Rate(None, "missing" if has_payer else "no_payer")


# --- code family: classify by the code's shape, not the declared type ---------------------------
# HCPCS Level I *is* CPT (AMA): Category I = 5 digits; Category II = 4 digits + F; Category III = 4 digits + T;
# PLA (proprietary lab analyses) = 4 digits + U; MAAA (multianalyte assays with algorithmic analyses) = 4 digits + M.
# HCPCS Level II (CMS) = one letter + 4 digits, where
# D-codes are CDT (dental). So "CPT typed as HCPCS" is technically valid; "A1234 typed as CPT" is not.

CODE_FAMILIES = frozenset({
    "CPT_CAT_I", "CPT_CAT_II", "CPT_CAT_III", "CPT_PLA", "CPT_MAAA", "HCPCS_II", "CDT", "UNCLASSIFIED",
})

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CPT_CAT_I", re.compile(r"\d{5}")),
    ("CPT_CAT_II", re.compile(r"\d{4}F")),
    ("CPT_CAT_III", re.compile(r"\d{4}T")),
    ("CPT_PLA", re.compile(r"\d{4}U")),
    ("CPT_MAAA", re.compile(r"\d{4}M")),
    ("CDT", re.compile(r"D\d{4}")),
    ("HCPCS_II", re.compile(r"[A-CEGHJ-MP-V]\d{4}")),
)


@dataclass(frozen=True)
class CodeClass:
    code: str
    declared_type: str | None
    code_family: str | None       # None for non-procedure code types (NDC, RC, MS-DRG, CDM, ...)
    type_conflict: str | None     # None | 'cpt_declared_not_cpt' | 'hcpcs_declared_unclassified' | 'invalid_type'


def classify_code(code: str, declared_type: str | None) -> CodeClass:
    c = code.strip().upper()
    t, valid = norm_enum(declared_type, CODE_TYPES, upper=True)
    if not valid:
        return CodeClass(c, t, None, "invalid_type")
    matched = next((name for name, pat in _PATTERNS if pat.fullmatch(c)), None)
    if t is None:  # type missing (logged by the parser): classify by shape alone, never force a family
        return CodeClass(c, None, matched, None)
    if t not in ("CPT", "HCPCS"):
        return CodeClass(c, t, None, None)
    family = matched or "UNCLASSIFIED"
    conflict = None
    if t == "CPT" and not family.startswith("CPT_"):
        conflict = "cpt_declared_not_cpt"
    elif t == "HCPCS" and family == "UNCLASSIFIED":
        conflict = "hcpcs_declared_unclassified"
    return CodeClass(c, t, family, conflict)

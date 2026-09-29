import pytest

from clear_pricer import rules
from clear_pricer.rules import Count, classify_code, clean_allowed_amounts, parse_count, resolve_rate


@pytest.mark.parametrize("raw,bucket,n", [
    ("0", "0", 0), ("1 through 10", "1-10", None), ("1 THROUGH 10 ", "1-10", None), ("11", "11+", 11),
    ("3048", "11+", 3048), ("", None, None), (None, None, None),
    ("5", "invalid", None), ("2,025", "invalid", None), ("n/a", "invalid", None),
])
def test_parse_count(raw, bucket, n):
    assert parse_count(raw) == Count(bucket, n)


def test_zero_count_nulls_allowed_amounts_not_zeroes_them():
    a = clean_allowed_amounts(Count("0", 0), 248.52, 6.0, 491.04)
    assert (a.median, a.p10, a.p90, a.suppressed_reason) == (None, None, None, "count_zero")


def test_nonzero_count_keeps_allowed_amounts():
    a = clean_allowed_amounts(Count("11+", 3048), 248.52, 6.0, 491.04)
    assert (a.median, a.p10, a.p90, a.suppressed_reason) == (248.52, 6.0, 491.04, None)


def test_zero_count_without_amounts_is_not_a_suppression():
    assert clean_allowed_amounts(Count("0", 0), None, None, None).suppressed_reason is None


@pytest.mark.parametrize("dollar,pct,algo,gross,payer,rate,basis", [
    (100.0, None, None, 500.0, "Aetna", 100.0, "dollar"),
    (1.16, 54.40, None, 2.14, "Aetna", 1.16, "dollar_from_percent"),       # real Rush row: 54.4% x 2.14
    (1.30, 61.0, None, 2.14, "Aetna", 1.30, "dollar_from_percent"),        # Rush truncates 1.3054 -> 1.30
    (1.31, 61.0, None, 2.14, "Aetna", 1.31, "dollar_from_percent"),        # ... others round it -> 1.31
    (1.32, 61.0, None, 2.14, "Aetna", 1.32, "dollar_percent_unreconciled"),  # 2 cents off is not rounding
    (203.04, 30.0, None, 432.0, "Aetna", 203.04, "dollar_percent_unreconciled"),  # real Rush row
    (50.0, 10.0, None, None, "Aetna", 50.0, "dollar_percent_unreconciled"),  # no gross to reconcile against
    (None, 58.0, None, 1000.0, "Aetna", None, "percent_only"),
    (None, None, "DRG x base rate", 1000.0, "Aetna", None, "algorithm_only"),
    (None, None, None, 1000.0, "Aetna", None, "missing"),
    (None, None, None, 1000.0, None, None, "no_payer"),
    (None, None, "  ", 1000.0, "  ", None, "no_payer"),
])
def test_resolve_rate(dollar, pct, algo, gross, payer, rate, basis):
    r = resolve_rate(payer, dollar, pct, algo, gross)
    assert (r.negotiated_rate, r.rate_basis) == (rate, basis)
    assert r.rate_basis in rules.RATE_BASES


@pytest.mark.parametrize("code,declared,family,conflict", [
    ("54324", "CPT", "CPT_CAT_I", None),
    ("0232T", "HCPCS", "CPT_CAT_III", None),    # UChicago: Level I typed as HCPCS -- valid, not a conflict
    ("99213", "HCPCS", "CPT_CAT_I", None),      # Rush pattern
    ("3074F", "CPT", "CPT_CAT_II", None),
    ("0001U", "cpt", "CPT_PLA", None),          # case-insensitive enum
    ("0018M", "HCPCS", "CPT_MAAA", None),       # UChicago: MAAA codes typed HCPCS
    ("7746A", "HCPCS", "UNCLASSIFIED", "hcpcs_declared_unclassified"),  # UChicago: fits no standard shape
    ("J1885", "HCPCS", "HCPCS_II", None),
    ("A4216", "CPT", "HCPCS_II", "cpt_declared_not_cpt"),  # NM pattern
    ("D0120", "HCPCS", "CDT", None),
    ("CASE-54324", "CPT", "UNCLASSIFIED", "cpt_declared_not_cpt"),
    ("ABC", "HCPCS", "UNCLASSIFIED", "hcpcs_declared_unclassified"),
    (" j1885 ", "HCPCS", "HCPCS_II", None),     # trimmed + upper-cased
    ("0250", "RC", None, None),
    ("00002-1433-80", "NDC", None, None),
    ("123", "OTHER", None, "invalid_type"),
])
def test_classify_code(code, declared, family, conflict):
    c = classify_code(code, declared)
    assert (c.code_family, c.type_conflict) == (family, conflict)


@pytest.mark.parametrize("raw,value,ok", [("12.5", 12.5, True), ("", None, True), ("$12", None, False),
                                          ("1,234.00", None, False), ("-3", -3.0, True)])
def test_parse_number(raw, value, ok):
    assert rules.parse_number(raw) == (value, ok)

"""The M7 analysis enforces its stated method (exclusions, comparable basis) and is byte-reproducible.

Runs on a tiny synthetic 'release': three hospitals, a handful of codes, crafted so each exclusion rule is visible.
"""

import json

import duckdb
import pytest

from clear_pricer import analysis

F = ("charge_id, item_id, hospital_id, description, setting, gross_charge, discounted_cash, negotiated_rate, "
     "rate_basis, methodology, payer_name")


@pytest.fixture
def release(tmp_path):
    con = duckdb.connect()
    con.sql(f"""CREATE TABLE fct_standard_charges AS SELECT * FROM (VALUES
        -- 99213 at all three: list 100 / 200 / 300 -> ratio 3.0
        ('a1', 'nm-i1', 'nm', 'Office visit', 'outpatient', 100.0, 70.0, NULL, 'no_payer', NULL, NULL),
        ('a2', 'rush-i1', 'rush', 'Office visit', 'both', 200.0, 100.0, 80.0, 'dollar', 'fee schedule', 'P1'),
        ('a3', 'rush-i1b', 'rush', 'Office visit', 'outpatient', 200.0, 100.0, 200.0, 'dollar', 'other', 'MA PLAN'),
        ('a4', 'uc-i1', 'uchicago', 'Office visit', 'outpatient', 300.0, 300.0, 40.0, 'dollar', 'fee schedule', 'P1'),
        ('a5', 'uc-i1', 'uchicago', 'Office visit', 'outpatient', 300.0, 300.0, 160.0, 'dollar', 'fee schedule', 'P2'),
        ('a6', 'uc-i1', 'uchicago', 'Office visit', 'outpatient', 300.0, 300.0, 400.0, 'dollar', 'fee schedule', 'P3'),
        -- NM case package carrying 99213 at a huge price: must be excluded
        ('a7', 'nm-case', 'nm', 'Whole case', 'outpatient', 99999.0, 1.0, NULL, 'no_payer', NULL, NULL),
        -- an unlisted code priced at all three: must be excluded
        ('a8', 'nm-u', 'nm', 'Unlisted procedure', 'outpatient', 1.0, 1.0, NULL, 'no_payer', NULL, NULL),
        ('a9', 'rush-u', 'rush', 'Unlisted procedure', 'outpatient', 1000.0, 1.0, NULL, 'no_payer', NULL, NULL),
        ('b1', 'uc-u', 'uchicago', 'Unlisted procedure', 'outpatient', 5.0, 1.0, NULL, 'no_payer', NULL, NULL),
        -- inpatient only: out of scope
        ('b2', 'nm-ip', 'nm', 'Inpatient thing', 'inpatient', 10.0, 10.0, NULL, 'no_payer', NULL, NULL)
      ) t({F})""")
    con.sql("""CREATE TABLE dim_charge_codes AS SELECT * FROM (VALUES
        ('nm-i1', 'nm', 1, '99213', 'CPT', 'CPT_CAT_I', NULL),
        ('rush-i1', 'rush', 1, '99213', 'HCPCS', 'CPT_CAT_I', NULL),
        ('rush-i1b', 'rush', 1, '99213', 'HCPCS', 'CPT_CAT_I', NULL),
        ('uc-i1', 'uchicago', 1, '99213', 'HCPCS', 'CPT_CAT_I', NULL),
        ('nm-case', 'nm', 1, 'CASE-99213', 'LOCAL', NULL, NULL),
        ('nm-case', 'nm', 2, '99213', 'CPT', 'CPT_CAT_I', NULL),
        ('nm-u', 'nm', 1, '64999', 'CPT', 'CPT_CAT_I', NULL),
        ('rush-u', 'rush', 1, '64999', 'CPT', 'CPT_CAT_I', NULL),
        ('uc-u', 'uchicago', 1, '64999', 'CPT', 'CPT_CAT_I', NULL),
        ('nm-ip', 'nm', 1, '99221', 'CPT', 'CPT_CAT_I', NULL)
      ) t(item_id, hospital_id, code_seq, code, declared_type, code_family, type_conflict)""")
    con.sql("CREATE TABLE files AS SELECT 'nm' AS hospital_id")
    for t in ("fct_standard_charges", "dim_charge_codes", "files"):
        con.sql(f"COPY {t} TO '{(tmp_path / f'{t}.parquet').as_posix()}' (FORMAT parquet)")
    (tmp_path / "manifest.json").write_text(json.dumps({"fingerprint": "f" * 64}), encoding="utf-8")
    return tmp_path


def test_method_is_enforced(release, tmp_path):
    r = analysis.run(str(release), out=tmp_path / "out")
    assert r["gross"]["codes_all3"] == 1                    # 99213 only: the unlisted code is gone
    assert r["gross"]["median"] == pytest.approx(3.0)       # 300 / 100 -- the $99,999 case package is excluded
    assert r["nm_case_items"] == 1
    # contracted: fee schedule only; the MA row with negotiated == list never counts
    assert r["basket"]["99213"]["rush"]["contracted"] == 80.0 and r["basket"]["99213"]["rush"]["payers"] == 1
    assert r["basket"]["99213"]["uchicago"]["contracted"] == 160.0    # median of 40 / 160 / 400
    assert r["within"]["uchicago"]["median_spread"] == pytest.approx(10.0)   # 400 / 40
    assert r["between"]["median_rush_over_uc"] == pytest.approx(0.5)         # 80 / 160
    assert r["rush_list_equals_negotiated"] == (1, 1)
    assert r["cash_policy"]["uchicago"]["share_cash_equals_list"] == pytest.approx(1.0)


def test_output_is_byte_reproducible(release, tmp_path):
    analysis.run(str(release), out=tmp_path / "a")
    analysis.run(str(release), out=tmp_path / "b")
    for rel in ("price-variation.md", "price-variation.json", "figures/list-price-ratio-light.png",
                "figures/basket-list-prices-dark.png"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel

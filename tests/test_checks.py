"""The release's grain contract, its check values, and the gates added with them (CP-DEC 020, 021).

A gate that never fires proves nothing, so each new gate runs against a clean miniature (zero rows) and against
planted defects (each must return rows), including values just inside and just outside the money tolerance.
verify-release is run against a real export of a miniature warehouse, then against tampered copies of it.
"""

import json
import re
import shutil
from pathlib import Path

import duckdb
import pytest
import yaml

from clear_pricer.checks import RELEASE_GRAIN, verify_dir
from clear_pricer.export import RELEASE_TABLES, export

REPO = Path(__file__).resolve().parents[1]
DBT_TESTS = REPO / "dbt" / "tests"


# ------------------------------------------------------------------------------------------------- grain contract

def _yml_grain() -> dict[str, list[str]]:
    out = {}
    for f in (REPO / "dbt" / "models").rglob("_*.yml"):
        for m in yaml.safe_load(f.read_text(encoding="utf-8")).get("models", []):
            for t in m.get("tests", []) or []:
                if isinstance(t, dict) and "grain" in t:
                    out[m["name"]] = [k.strip('"') for k in t["grain"]["arguments"]["key"]]
    return out


def test_release_grain_matches_the_dbt_gates():
    """One contract: the keys verify-release checks are exactly the keys dbt gates (and docs/grain.md documents)."""
    assert _yml_grain() == {t: list(k) for t, k in RELEASE_GRAIN.items()}
    assert set(RELEASE_TABLES) == set(RELEASE_GRAIN)


def test_grain_doc_names_every_release_table():
    from clear_pricer.publish import served_name

    doc = (REPO / "docs" / "grain.md").read_text(encoding="utf-8")
    for t, key in RELEASE_GRAIN.items():
        assert f"`{served_name(t)}`" in doc, t
        assert all(f"`{k}`" in doc for k in key), (t, key)


# ------------------------------------------------------------------------------------------- gates fire when they should

def _gate(name: str) -> str:
    sql = (DBT_TESTS / f"{name}.sql").read_text(encoding="utf-8")
    return re.sub(r"\{\{\s*ref\('(\w+)'\)\s*\}\}", r"\1", sql)


def _rows(con, name: str) -> int:
    return len(con.sql(_gate(name)).fetchall())


@pytest.fixture
def history():
    con = duckdb.connect()
    con.sql("""CREATE TABLE dim_provider_history AS SELECT * FROM (VALUES
        ('1000000001', 1, DATE '2020-01-01', DATE '2024-05-01', false),
        ('1000000001', 2, DATE '2024-05-01', DATE '2024-05-01', false),  -- zero-length: valid on no day (allowed)
        ('1000000001', 3, DATE '2024-05-01', NULL, true),
        ('1000000002', 1, DATE '2019-03-02', NULL, true)
    ) t(npi, version, valid_from, valid_to, is_current)""")
    return con


def test_history_gate_clean(history):
    assert _rows(history, "assert_provider_history_no_overlap") == 0


@pytest.mark.parametrize("mutation", [
    "UPDATE dim_provider_history SET valid_to = DATE '2024-06-01' WHERE npi = '1000000001' AND version = 1",  # overlap
    "UPDATE dim_provider_history SET valid_to = DATE '2024-04-01' WHERE npi = '1000000001' AND version = 1",  # gap
    "UPDATE dim_provider_history SET valid_to = NULL WHERE npi = '1000000001' AND version = 1",  # two open versions
])
def test_history_gate_fires(history, mutation):
    history.sql(mutation)
    assert _rows(history, "assert_provider_history_no_overlap") > 0


@pytest.fixture
def prices():
    con = duckdb.connect()
    con.sql("""CREATE TABLE fct_standard_charges AS SELECT * FROM (VALUES
        ('c1', 'i1', 'rush', 'outpatient', 'dollar', 100.00),
        ('c2', 'i1', 'rush', 'outpatient', 'dollar', 250.00),
        ('c3', 'i2', 'rush', NULL, 'dollar_from_percent', 80.00),
        ('c4', 'i3', 'rush', 'inpatient', 'dollar', 999.00)       -- item i3 has no qualifying code
    ) t(charge_id, item_id, hospital_id, setting, rate_basis, negotiated_rate)""")
    con.sql("""CREATE TABLE dim_charge_codes AS SELECT * FROM (VALUES
        ('i1', 'rush', 1, '99213', 'CPT', 'CPT_CAT_I'),
        ('i1', 'rush', 2, '0510', 'RC', NULL),
        ('i2', 'rush', 1, 'J1100', 'HCPCS', 'HCPCS_II'),
        ('i3', 'rush', 1, '0450', 'RC', NULL)
    ) t(item_id, hospital_id, code_seq, code, declared_type, code_family)""")
    con.sql("""CREATE TABLE agg_code_prices AS SELECT * FROM (VALUES
        ('rush', 'CPT_CAT_I', '99213', 'outpatient', 'dollar', 2, 100.00, 250.00),
        ('rush', 'HCPCS_II', 'J1100', 'unspecified', 'dollar_from_percent', 1, 80.00, 80.00)
    ) t(hospital_id, code_family, code, setting, rate_basis, charge_rows, rate_min, rate_max)""")
    return con


def test_header_vs_lines_gate_clean(prices):
    assert _rows(prices, "assert_agg_code_prices_match_lines") == 0


def test_header_vs_lines_gate_tolerates_float_noise(prices):
    prices.sql("UPDATE agg_code_prices SET rate_max = 250.004 WHERE code = '99213'")  # inside half a cent
    assert _rows(prices, "assert_agg_code_prices_match_lines") == 0


@pytest.mark.parametrize("mutation", [
    "UPDATE agg_code_prices SET charge_rows = 3 WHERE code = '99213'",                   # header overcounts
    "DELETE FROM agg_code_prices WHERE code = 'J1100'",                                  # lines without a header
    "INSERT INTO agg_code_prices VALUES ('rush', 'CDT', 'D0120', 'outpatient', 'dollar', 1, 5, 5)",  # header, no lines
    "UPDATE agg_code_prices SET rate_max = 250.01 WHERE code = '99213'",                 # money outside tolerance
    "UPDATE agg_code_prices SET rate_min = NULL WHERE code = 'J1100'",                   # NULL vs a value
    "DELETE FROM fct_standard_charges WHERE charge_id = 'c2'",                           # a line lost upstream
])
def test_header_vs_lines_gate_fires(prices, mutation):
    prices.sql(mutation)
    assert _rows(prices, "assert_agg_code_prices_match_lines") > 0


@pytest.fixture
def claims():
    con = duckdb.connect()
    con.sql("""CREATE TABLE stg_fhir__claims AS SELECT * FROM (VALUES ('k1', 120.50), ('k2', 40.00))
               t(resource_id, total)""")
    con.sql("""CREATE TABLE stg_fhir__claim_items AS SELECT * FROM (VALUES ('k1', 1), ('k1', 2), ('k2', 1))
               t(claim_id, sequence)""")
    con.sql("""CREATE TABLE stg_fhir__eobs AS SELECT * FROM (VALUES ('e1', 'k1', 120.50), ('e2', 'k2', 40.00))
               t(resource_id, claim_id, total_amount)""")
    return con


def test_claim_gate_clean(claims):
    assert _rows(claims, "assert_fhir_claims_match_eobs") == 0
    claims.sql("UPDATE stg_fhir__eobs SET total_amount = 120.504 WHERE claim_id = 'k1'")  # inside tolerance
    assert _rows(claims, "assert_fhir_claims_match_eobs") == 0


@pytest.mark.parametrize("mutation", [
    "INSERT INTO stg_fhir__claim_items VALUES ('k9', 1)",                     # line without a header
    "DELETE FROM stg_fhir__claim_items WHERE claim_id = 'k2'",                # header without lines
    "DELETE FROM stg_fhir__eobs WHERE claim_id = 'k2'",                       # claim without an EOB
    "INSERT INTO stg_fhir__eobs VALUES ('e3', 'k1', 120.50)",                 # two EOBs for one claim
    "INSERT INTO stg_fhir__eobs VALUES ('e4', 'k9', 1.00)",                   # EOB without a claim
    "UPDATE stg_fhir__eobs SET total_amount = 120.51 WHERE claim_id = 'k1'",  # totals differ by a cent
])
def test_claim_gate_fires(claims, mutation):
    claims.sql(mutation)
    assert _rows(claims, "assert_fhir_claims_match_eobs") > 0


# ------------------------------------------------------------------------------------------ export + verify-release

_MINI = {
    "fct_standard_charges": "SELECT * FROM (VALUES ('c1', 'i1', 100.25, 300.0), ('c2', 'i2', 80.0, NULL)) "
                            "t(charge_id, item_id, negotiated_rate, gross_charge)",
    "dim_charge_codes": "SELECT * FROM (VALUES ('i1', 1, '99213', 'CPT', 'CPT_CAT_I'), ('i1', 2, '0510', 'RC', NULL), "
                        "('i2', 1, 'J1100', 'HCPCS', 'HCPCS_II')) t(item_id, code_seq, code, declared_type, code_family)",
    "agg_code_prices": "SELECT * FROM (VALUES ('rush', 'CPT_CAT_I', '99213', 'outpatient', 'dollar', 1), "
                       "('rush', 'HCPCS_II', 'J1100', 'outpatient', 'dollar', 1)) "
                       "t(hospital_id, code_family, code, setting, rate_basis, charge_rows)",
    "rpt_npi_reconciliation": "SELECT * FROM (VALUES ('rush', 0.0, 0.5), ('ALL', 0.0, 0.5)) "
                              "t(hospital_id, unresolved_rate, disclosure_coverage)",
    "rpt_npi_resolution": "SELECT 'rush' AS hospital_id, '1000000001' AS npi",
    "rpt_npi_completeness": "SELECT 'rush' AS hospital_id, '1000000001' AS npi",
    "rpt_source_conformance": "SELECT 'rush' AS hospital_id, 'value_not_in_enum' AS kind, 'setting' AS \"column\"",
    "stg_hpt__files": "SELECT 'rush' AS hospital_id, 'abc123' AS source_sha256, '2026-09-01' AS last_updated_on",
    "rpt_nppes_file_log": "SELECT 1 AS seq, 'NPPES_full.zip' AS source_file, 'applied' AS outcome",
    "dim_modifiers": "SELECT 'rush' AS hospital_id, '25' AS code, NULL::VARCHAR AS setting, "
                     "NULL::VARCHAR AS payer_name, NULL::VARCHAR AS plan_name",
    "rpt_fhir_summary": "SELECT 'Claim' AS resource_type",
    "rpt_fhir_mapping": "SELECT 'Claim' AS resource_type, 'total.value' AS path",
    "rpt_fhir_code_bridge": "SELECT 'http://www.ada.org/cdt' AS code_system",
    "rpt_fhir_claim_totals": "SELECT 'pharmacy' AS claim_type",
    "dim_provider_history": "SELECT * FROM (VALUES ('1000000001', 1, DATE '2020-01-01', DATE '2024-05-01', false), "
                            "('1000000001', 2, DATE '2024-05-01', NULL, true)) "
                            "t(npi, version, valid_from, valid_to, is_current)",
    "stg_fhir__run": "SELECT 'v4.0.0' AS synthea_version, 'jar' AS jar_sha256, '-s 42' AS args",
}


def _warehouse(path: Path, overrides: dict | None = None) -> Path:
    con = duckdb.connect(str(path))
    for t, sql in {**_MINI, **(overrides or {})}.items():
        con.sql(f"CREATE TABLE {t} AS {sql}")
    con.close()
    return path


@pytest.fixture(scope="module")
def release_dir(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("release")
    export(_warehouse(tmp / "wh.duckdb"), tmp / "published", log=lambda m: None)
    return tmp / "published"


def _copy(src: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "copy"
    shutil.copytree(src, dst)
    return dst


def test_export_writes_check_values(release_dir):
    cv = json.loads((release_dir / "check_values.json").read_text(encoding="utf-8"))
    d = cv["derived"]
    assert cv["tables"]["fct_standard_charges"] == {"key": ["charge_id"], "rows": 2, "distinct_keys": 2}
    assert d["charge_x_code_rows"] == 3 and d["charges_to_codes_fanout"] == 1.5
    assert d["negotiated_rate_sum_cents"] == 18025
    assert d["provider_history_versions_valid_on_one_day"] == 0


def test_verify_passes_on_an_untouched_release(release_dir):
    assert verify_dir(release_dir, log=lambda m: None) == []


def test_verify_catches_a_changed_byte(release_dir, tmp_path):
    d = _copy(release_dir, tmp_path)
    p = d / "fct_standard_charges.parquet"
    b = bytearray(p.read_bytes())
    b[len(b) // 2] ^= 0xFF
    p.write_bytes(bytes(b))
    assert any("sha256" in f for f in verify_dir(d, log=lambda m: None))


def test_verify_catches_data_rewritten_with_a_matching_manifest(release_dir, tmp_path):
    """Someone rewrites a file *and* its manifest hash: the recomputed check values still disagree."""
    import hashlib

    d = _copy(release_dir, tmp_path)
    p = d / "fct_standard_charges.parquet"
    duckdb.sql(f"COPY (SELECT * FROM read_parquet('{p.as_posix()}') WHERE charge_id = 'c1') "
               f"TO '{(d / 'x.parquet').as_posix()}' (FORMAT parquet)")
    (d / "x.parquet").replace(p)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    for f in m["files"]:
        if f["file"] == p.name:
            f["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    failures = verify_dir(d, log=lambda m: None)
    assert any("fct_standard_charges.rows" in f for f in failures)


def test_verify_catches_an_edited_check_value(release_dir, tmp_path):
    d = _copy(release_dir, tmp_path)
    cv = json.loads((d / "check_values.json").read_text(encoding="utf-8"))
    cv["derived"]["charges"] = 3
    (d / "check_values.json").write_text(json.dumps(cv), encoding="utf-8")
    failures = verify_dir(d, log=lambda m: None)
    assert any("check_values.json: sha256" in f for f in failures)
    assert any("derived.charges" in f for f in failures)


def test_export_refuses_an_overlapping_history(tmp_path):
    bad = ("SELECT * FROM (VALUES ('1000000001', 1, DATE '2020-01-01', DATE '2024-06-01', false), "
           "('1000000001', 2, DATE '2024-05-01', NULL, true)) t(npi, version, valid_from, valid_to, is_current)")
    wh = _warehouse(tmp_path / "wh.duckdb", {"dim_provider_history": bad})
    with pytest.raises(RuntimeError, match="valid on one day"):
        export(wh, tmp_path / "published", log=lambda m: None)


def test_export_refuses_a_duplicate_key(tmp_path):
    wh = _warehouse(tmp_path / "wh.duckdb", {"rpt_fhir_summary": "SELECT 'Claim' AS resource_type UNION ALL "
                                                                 "SELECT 'Claim'"})
    with pytest.raises(RuntimeError, match="distinct keys"):
        export(wh, tmp_path / "published", log=lambda m: None)

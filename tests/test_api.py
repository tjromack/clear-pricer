"""Read API over published Parquet: endpoints, filters, parameterisation. Uses a tiny synthetic 'release'."""

import json

import duckdb
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    con = duckdb.connect()
    con.sql("""CREATE TABLE agg_code_prices AS SELECT * FROM (VALUES
        ('rush', 'CPT_CAT_I', '99213', 'outpatient', 'dollar', 3, 2, 50.0, 60.0, 70.0, 80.0, 90.0, 200.0, 100.0, 'VISIT'),
        ('nm', 'CPT_CAT_I', '99213', 'outpatient', 'dollar_from_percent', 1, 1, 10.0, 10.0, 10.0, 10.0, 10.0, 20.0, 5.0, 'VISIT'))
        t(hospital_id, code_family, code, setting, rate_basis, charge_rows, payer_plans, rate_min, rate_p25, rate_median,
          rate_p75, rate_max, gross_median, cash_median, example_description)""")
    con.sql("""CREATE TABLE rpt_npi_reconciliation AS SELECT * FROM (VALUES
        ('rush', 1, 0, 1, 22, 0, 22, 0.0, 1.0, 0.0435), ('ALL', 1, 0, 1, 22, 0, 22, 0.0, 1.0, 0.0435))
        t(hospital_id, disclosed_npis, unresolved_npis, verified_npis, undisclosed_candidates, undisclosed_tier1,
          undisclosed_tier2, unresolved_rate, verified_rate, disclosure_coverage)""")
    for f in ("agg_code_prices", "rpt_npi_reconciliation"):
        con.sql(f"COPY {f} TO '{(tmp_path / f'{f}.parquet').as_posix()}' (FORMAT parquet)")
    (tmp_path / "manifest.json").write_text(json.dumps({"fingerprint": "abc", "files": [
        {"file": "agg_code_prices.parquet", "rows": 2}]}), encoding="utf-8")
    from clear_pricer import api
    monkeypatch.setattr(api, "PUBLISHED", tmp_path)
    monkeypatch.setattr(api._local, "con", None, raising=False)
    return TestClient(api.app)


def test_health_reports_the_release_fingerprint(client):
    assert client.get("/health").json()["fingerprint"] == "abc"


def test_prices_filter_by_rate_basis(client):
    all_rows = client.get("/prices/99213").json()
    assert {r["hospital_id"] for r in all_rows} == {"rush", "nm"}
    contracted = client.get("/prices/99213", params={"rate_basis": "dollar"}).json()
    assert [(r["hospital_id"], r["rate_median"]) for r in contracted] == [("rush", 70.0)]
    assert client.get("/prices/99213", params={"rate_basis": "bogus"}).status_code == 422


def test_reconciliation_headline_last_row_is_all(client):
    rows = client.get("/reconciliation").json()
    assert rows[-1]["hospital_id"] == "ALL"


def test_inputs_are_parameters_not_sql(client):
    """A hostile code is just a value that matches nothing -- never executed."""
    assert client.get("/prices/99213' OR '1'='1").json() == []

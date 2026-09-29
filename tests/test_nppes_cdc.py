"""NPPES CDC: idempotent re-application, delta-only updates, ordering, deactivation stubs, schema rejection.

Synthetic records on the real public V2 header (tests/fixtures/nppes_v2_fileheader.csv); NPIs are fake.
"""

import csv
import io
import zipfile
from pathlib import Path

import duckdb
import pytest

from clear_pricer import nppes

FIX = Path(__file__).parent / "fixtures"
HEADER = next(csv.reader(io.StringIO((FIX / "nppes_v2_fileheader.csv").read_text(encoding="utf-8"))))


def rec(npi, *, last="SMITH", first="ANN", city="CHICAGO", updated="01/15/2026", entity="1", tax="207R00000X",
        deact="", react="", stub=False):
    r = dict.fromkeys(HEADER, "")
    r["NPI"] = npi
    if stub:  # how NPPES publishes a deactivation: NPI + date, everything else blank
        r["NPI Deactivation Date"] = deact
        return r
    r |= {"Entity Type Code": entity, "Provider Last Name (Legal Name)": last, "Provider First Name": first,
          "Provider Business Practice Location Address City Name": city, "Last Update Date": updated,
          "Provider Enumeration Date": "05/01/2010", "Healthcare Provider Taxonomy Code_1": tax,
          "Healthcare Provider Primary Taxonomy Switch_1": "Y", "NPI Deactivation Date": deact,
          "NPI Reactivation Date": react}
    return r


def make_zip(tmp: Path, name: str, span: str, rows: list[dict], header=HEADER) -> Path:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, quoting=csv.QUOTE_ALL, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    path = tmp / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"npidata_pfile_{span}.csv", buf.getvalue())
    return path


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "nppes.duckdb"

    def load(name, kind, span, rows, header=HEADER):
        z = make_zip(tmp_path, name, span, rows, header)
        staged = nppes.stage_zip(z, kind, tmp_path / "staging")
        return nppes.apply(state, staged, name)

    def q(sql):
        con = duckdb.connect(str(state), read_only=True)
        try:
            return con.sql(sql).fetchall()
        finally:
            con.close()

    return load, q


FULL = [rec("1000000001"), rec("1000000002", last="JONES"), rec("1000000003", last="LEE")]


def test_full_then_reapply_is_idempotent(env):
    load, q = env
    first = load("full_A.zip", "full", "20050523-20260913", FULL)
    assert (first["inserted"], first["updated"], first["unchanged"]) == (3, 0, 0)
    snapshot = q("SELECT * FROM provider_history ORDER BY npi, version")
    again = load("full_A.zip", "full", "20050523-20260913", FULL)
    assert (again["inserted"], again["updated"], again["unchanged"], again["absent_from_full"]) == (0, 0, 3, 0)
    assert q("SELECT * FROM provider_history ORDER BY npi, version") == snapshot


def test_weekly_delta_touches_only_its_records(env):
    load, q = env
    load("full_A.zip", "full", "20050523-20260913", FULL)
    weekly = [rec("1000000001", city="EVANSTON", updated="09/15/2026"),        # moved -> update
              rec("1000000002", deact="09/16/2026", stub=True),                  # deactivated (stub)
              rec("1000000004", last="NEW", updated="09/17/2026")]               # new NPI
    r = load("w1.zip", "weekly", "20260914-20260920", weekly)
    assert (r["updated"], r["deactivated"], r["inserted"], r["absent_from_full"]) == (1, 1, 1, 0)
    # 1000000003 was not in the delta: untouched, still version 1 and current
    assert q("SELECT version, is_current FROM provider_history WHERE npi = '1000000003'") == [(1, True)]
    # the move: old version closed at the change date, new version current
    assert q("SELECT version, practice_city, valid_from, valid_to IS NULL, is_current FROM provider_history "
             "WHERE npi = '1000000001' ORDER BY version") == [
        (1, "CHICAGO", q("SELECT DATE '2026-01-15'")[0][0], False, False),
        (2, "EVANSTON", q("SELECT DATE '2026-09-15'")[0][0], True, True)]
    # the deactivation stub kept the provider's last known identity instead of blanking it
    assert q("SELECT last_name, status, change_type FROM provider_history "
             "WHERE npi = '1000000002' AND is_current") == [("JONES", "deactivated", "deactivate")]
    snapshot = q("SELECT * FROM provider_history ORDER BY npi, version")
    again = load("w1.zip", "weekly", "20260914-20260920", weekly)
    assert (again["updated"], again["deactivated"], again["inserted"], again["unchanged"]) == (0, 0, 0, 3)
    assert q("SELECT * FROM provider_history ORDER BY npi, version") == snapshot


def test_older_record_never_overwrites_newer_state(env):
    load, q = env
    load("full_A.zip", "full", "20050523-20260913", FULL)
    load("w1.zip", "weekly", "20260914-20260920", [rec("1000000001", city="EVANSTON", updated="09/15/2026")])
    r = load("w2.zip", "weekly", "20260921-20260927", [rec("1000000001", city="OLDVILLE", updated="09/01/2026")])
    assert r["stale_skipped"] == 1
    assert q("SELECT practice_city FROM provider_history WHERE npi = '1000000001' AND is_current") == [("EVANSTON",)]


def test_weekly_older_than_the_full_is_superseded(env):
    load, q = env
    load("full_A.zip", "full", "20050523-20260913", FULL)
    assert load("w0.zip", "weekly", "20260907-20260913", [rec("1000000001", city="X")])["outcome"] == "superseded_by_full"
    assert q("SELECT count(*) FROM provider_history") == [(3,)]


def test_reactivation_and_absent_from_full(env):
    load, q = env
    load("full_A.zip", "full", "20050523-20260913", FULL)
    load("w1.zip", "weekly", "20260914-20260920", [rec("1000000002", deact="09/16/2026", stub=True)])
    r = load("w2.zip", "weekly", "20260921-20260927",
             [rec("1000000002", last="JONES", deact="09/16/2026", react="09/22/2026", updated="09/22/2026")])
    assert r["reactivated"] == 1
    assert q("SELECT status FROM provider_history WHERE npi = '1000000002' AND is_current") == [("active",)]
    # next month's full no longer lists 1000000003 -> tombstoned, not deleted
    full_b = [rec("1000000001"), rec("1000000002", last="JONES", deact="09/16/2026", react="09/22/2026",
                                      updated="09/22/2026")]
    r = load("full_B.zip", "full", "20050523-20261011", full_b)
    assert r["absent_from_full"] == 1
    assert q("SELECT status, is_current FROM provider_history WHERE npi = '1000000003' ORDER BY version") == [
        ("active", False), ("absent_from_full", True)]
    assert load("full_B.zip", "full", "20050523-20261011", full_b)["absent_from_full"] == 0  # idempotent


def test_reapplying_an_older_full_after_deltas_changes_nothing(env):
    """Regression: an NPI created by a later weekly is not 'absent' from an older full file."""
    load, q = env
    load("full_A.zip", "full", "20050523-20260913", FULL)
    load("w1.zip", "weekly", "20260914-20260920", [rec("1000000001", city="EVANSTON", updated="09/15/2026"),
                                                    rec("1000000004", last="NEW", updated="09/17/2026")])
    snapshot = q("SELECT * FROM provider_history ORDER BY npi, version")
    r = load("full_A.zip", "full", "20050523-20260913", FULL)
    assert (r["absent_from_full"], r["inserted"], r["updated"], r["stale_skipped"]) == (0, 0, 0, 1)
    assert q("SELECT * FROM provider_history ORDER BY npi, version") == snapshot


def test_same_date_record_across_file_boundary_resolves_to_the_newer_file(env):
    """Regression (real NPPES): the Sept full file 'through 09/13' carried NPI records dated 09/14 that the 09/14 weekly
    also carried, with different content. Re-applying files in any order must converge on the weekly's version."""
    load, q = env
    full = [rec("1000000001", city="FULLCITY", updated="09/14/2026")]
    week = [rec("1000000001", city="WEEKCITY", updated="09/14/2026")]
    load("full_A.zip", "full", "20050523-20260913", full)
    load("w1.zip", "weekly", "20260914-20260920", week)
    snapshot = q("SELECT * FROM provider_history ORDER BY npi, version")
    assert q("SELECT practice_city FROM provider_history WHERE is_current") == [("WEEKCITY",)]
    assert load("full_A.zip", "full", "20050523-20260913", full)["stale_skipped"] == 1
    assert load("w1.zip", "weekly", "20260914-20260920", week)["unchanged"] == 1
    assert q("SELECT * FROM provider_history ORDER BY npi, version") == snapshot


def test_history_invariants_hold(env):
    load, q = env
    load("full_A.zip", "full", "20050523-20260913", FULL)
    load("w1.zip", "weekly", "20260914-20260920", [rec("1000000001", city="EVANSTON", updated="09/15/2026"),
                                                    rec("1000000002", deact="09/16/2026", stub=True)])
    assert q("SELECT npi FROM provider_history GROUP BY npi HAVING count(*) FILTER (WHERE is_current) <> 1") == []
    assert q("SELECT npi FROM provider_history GROUP BY npi HAVING max(version) <> count(*)") == []


def test_missing_projected_column_is_rejected_not_loaded(env):
    load, q = env
    header = [h for h in HEADER if h != "Last Update Date"]
    r = load("bad.zip", "weekly", "20260914-20260920", [rec("1000000001")], header=header)
    assert r["outcome"] == "rejected_schema"
    assert q("SELECT kind, \"column\", n FROM drift_log ORDER BY kind") == [
        ("column_count_changed", "", 329), ("missing_required_column", "Last Update Date", 1)]
    assert q("SELECT count(*) FROM provider_history") == [(0,)]


def test_listing_and_coverage():
    html = ("<a href='./NPPES_Data_Dissemination_September_2026_V2.zip'>x</a>"
            "<a href='./NPPES_Deactivated_NPI_Report_091426_V2.zip'>d</a>"
            "<a href='./NPPES_Data_Dissemination_092126_092726_Weekly_V2.zip'>w</a>")
    assert [(f.name, f.kind) for f in nppes.parse_listing(html)] == [
        ("NPPES_Data_Dissemination_September_2026_V2.zip", "full"),
        ("NPPES_Data_Dissemination_092126_092726_Weekly_V2.zip", "weekly")]
    s, e, m = nppes.coverage(["x.pdf", "npidata_pfile_20050523-20260913.csv"])
    assert (str(s), str(e), m) == ("2005-05-23", "2026-09-13", "npidata_pfile_20050523-20260913.csv")

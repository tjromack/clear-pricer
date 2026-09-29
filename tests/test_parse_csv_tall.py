import io
import json
from pathlib import Path

from clear_pricer.parse_csv_tall import CsvTallParse

FIX = Path(__file__).parent / "fixtures"


class Result:
    """Collect a streaming parse into lists for assertions."""

    def __init__(self, lines):
        p = CsvTallParse(lines)
        self.charges, self.codes, self.quarantine = [], [], []
        for kind, row in p.events():
            {"charge": self.charges, "code": self.codes, "quarantine": self.quarantine}[kind].append(row)
        self.meta, self.drift, self.records_read = p.meta, p.drift, p.records_read


def parse(lines):
    return Result(lines)


def _parse_file(name: str):
    with (FIX / name).open(encoding="utf-8-sig", newline="") as f:
        return parse(f)


def _kinds(result) -> dict[tuple[str, str], int]:
    return {(e["kind"], e["column"]): e["n"] for e in result.drift.rows()}


def test_cms_official_example_maps_fully():
    r = _parse_file("cms_v3_tall_example.csv")
    kinds = {k for k, _ in _kinds(r)}
    assert r.meta.version == "3.0.0"
    assert r.meta.type_2_npi == ["0000000001", "0000000002", "0000000003"]
    assert r.meta.license_state == "CA"
    assert r.records_read == len(r.charges) + len(r.quarantine) > 0
    assert not r.quarantine
    assert not kinds & {"missing_required_column", "missing_required_meta", "unmapped_column", "ragged_row"}


def test_rush_sample_maps_and_logs_known_drift():
    r = _parse_file("rush_sample.csv")
    assert r.meta.hospital_name == "RUSH University Medical Center"
    assert r.meta.type_2_npi == ["1932213600"]
    assert r.meta.license_state == "IL"            # header was "license_number | IL" (spaces tolerated)
    assert r.records_read == len(r.charges) == 20
    kinds = _kinds(r)
    assert ("missing_required_column", "standard_charge|min") not in kinds
    assert not any(k == "missing_required_column" for k, _ in kinds)
    first = r.charges[0]                               # AETNA COMM row: dollar 1.16 = 54.40% x 2.14
    assert (first["rate_basis"], first["negotiated_rate"]) == ("dollar_from_percent", 1.16)
    assert first["count_bucket"] == "11+" and first["count_n"] == 3048


MINI_HEADER = (
    'hospital_name,last_updated_on,version,location_name,hospital_address,license_number|IL,type_2_npi,'
    '"To the best of its knowledge and belief, this hospital ...",attester_name\n'
    'Test Hospital,2026-01-01,3.0.0,Test Hospital,1 Main St,123,1234567893,true,A B\n'
)
DATA_COLS = ("description,code|1,code|1|type,code|2,code|2|type,setting,modifiers,drug_unit_of_measurement,"
             "drug_type_of_measurement,standard_charge|gross,standard_charge|discounted_cash,payer_name,plan_name,"
             "standard_charge|negotiated_dollar,standard_charge|negotiated_percentage,"
             "standard_charge|negotiated_algorithm,median_amount,10th_percentile,90th_percentile,count,"
             "standard_charge|methodology,standard_charge|min,standard_charge|max,additional_generic_notes,"
             "hospital_internal_flag\n")


def _row(**kw) -> str:
    cols = DATA_COLS.strip().split(",")
    return ",".join(kw.get(c.replace("|", "_"), "") for c in cols) + "\n"


def test_drift_is_recorded_never_dropped():
    text = MINI_HEADER + DATA_COLS + "".join([
        # count 0 with a median -> clean median nulled, raw kept
        _row(description="A", code_1="99213", code_1_type="HCPCS", setting="Outpatient", payer_name="P",
             plan_name="Q", **{"standard_charge_gross": "200", "standard_charge_negotiated_percentage": "50",
                               "median_amount": "80", "count": "0", "standard_charge_methodology": "other",
                               "hospital_internal_flag": "Y"}),
        # CPT-declared HCPCS Level II code, bad numeric, bad enum
        _row(description="B", code_1="A4216", code_1_type="CPT", code_2="0250", code_2_type="RC",
             setting="clinic", **{"standard_charge_gross": "$12"}),
        # code without a type
        _row(description="C", code_1="J1885", **{"standard_charge_gross": "5"}),
        "only,three,cells\n",  # ragged -> quarantined
    ])
    r = parse(io.StringIO(text))
    k = _kinds(r)
    assert r.records_read == 4 and len(r.charges) == 3 and len(r.quarantine) == 1
    assert k[("unmapped_column", "hospital_internal_flag")] == 1
    assert json.loads(r.charges[0]["unmapped_json"]) == {"hospital_internal_flag": "Y"}
    a = r.charges[0]
    assert (a["median_amount_raw"], a["median_amount"], a["allowed_amounts_suppressed"]) == (80.0, None, "count_zero")
    assert a["setting"] == "outpatient" and a["rate_basis"] == "percent_only"
    assert k[("allowed_amounts_with_zero_count", "median_amount")] == 1
    assert k[("code_cpt_declared_not_cpt", "code_type")] == 1
    assert k[("unparseable_numeric", "standard_charge|gross")] == 1
    assert k[("invalid_enum", "setting")] == 1 and r.charges[1]["setting"] is None
    assert k[("code_type_pair_incomplete", "code|1")] == 1
    assert k[("ragged_row", "")] == 1
    fams = {(c["item_locator"], c["code"]): c["code_family"] for c in r.codes}
    assert fams[("r4", "99213")] == "CPT_CAT_I" and fams[("r5", "A4216")] == "HCPCS_II" and fams[("r5", "0250")] is None
    assert fams[("r6", "J1885")] == "HCPCS_II"          # classified by shape even with no declared type


def test_missing_required_column_is_logged():
    header = DATA_COLS.replace("count,", "")
    r = parse(io.StringIO(MINI_HEADER + header + "X,1,CPT\n"))
    assert ("missing_required_column", "count") in _kinds(r)


def test_truncated_file():
    r = parse(io.StringIO("hospital_name\n"))
    assert ("truncated_header", "") in _kinds(r) and r.records_read == 0

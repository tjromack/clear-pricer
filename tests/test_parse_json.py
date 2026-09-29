import io
import json
from pathlib import Path

from clear_pricer.parse_json import JsonParse

FIX = Path(__file__).parent / "fixtures"


def run(data: bytes):
    p = JsonParse(io.BytesIO(data))
    out = {"charge": [], "code": [], "modifier": [], "quarantine": []}
    for kind, row in p.events():
        out[kind].append(row)
    return p, out


def kinds(p) -> dict[tuple[str, str], int]:
    return {(e["kind"], e["column"]): e["n"] for e in p.drift.rows()}


def test_cms_official_example_maps_fully():
    raw = (FIX / "cms_v3_json_example.json").read_bytes()
    doc = json.loads(raw.decode("utf-8-sig"))
    p, out = run(raw.removeprefix(b"\xef\xbb\xbf"))
    expected_rows = sum(len(sc.get("payers_information") or [None])
                        for it in doc["standard_charge_information"] for sc in it["standard_charges"])
    assert p.meta.version == "3.0.0" and p.meta.attestation == "true"
    assert p.meta.type_2_npi == ["0000000001", "0000000002", "0000000003"]
    assert p.records_read == len(out["charge"]) == expected_rows
    assert not out["quarantine"]
    assert len(out["modifier"]) > 0
    bad = {k for k, _ in kinds(p)} & {"missing_required_meta", "missing_required_field", "unmapped_field",
                                      "item_unmappable", "unmapped_meta_column"}
    assert not bad, bad


def _doc(items, **top):
    base = {"hospital_name": "H", "last_updated_on": "2026-01-01", "version": "3.0.0", "location_name": ["H"],
            "hospital_address": ["1 Main"], "type_2_npi": ["1234567893"], "license_information": {"state": "il"},
            "attestation": {"attestation": "...", "confirm_attestation": True, "attester_name": "A B"}}
    return json.dumps(base | top | {"standard_charge_information": items}).encode()


def test_drift_is_recorded_never_dropped():
    items = [
        {"description": "A", "code_information": [{"code": "0232T", "type": "HCPCS"}, {"code": "A4216", "type": "CPT"}],
         "standard_charges": [{"setting": "both", "gross_charge": 1177.0, "billing_class": "facility",
                               "vendor_extra": 1,
                               "payers_information": [
                                   {"payer_name": "P", "plan_name": "Q", "methodology": "percent of total billed charges",
                                    "standard_charge_percentage": 58.0, "standard_charge_dollar": 682.66,
                                    "median_amount": 25805.1, "count": "0"},
                                   {"payer_name": "P2", "plan_name": "Q2", "methodology": "fee schedule",
                                    "standard_charge_dollar": "12.50", "count": "1 through 10"}]}]},
        {"description": "B", "code_information": [], "standard_charges": [{"setting": "outpatient",
                                                                         "gross_charge": 5}]},
        "not an object",
    ]
    p, out = run(_doc(items, extra_top={"x": 1}))
    k = kinds(p)
    assert p.records_read == 4 and len(out["charge"]) == 3 and len(out["quarantine"]) == 1
    a, a2, b = out["charge"]
    assert a["source_locator"] == "i0.s0.p0" and a["item_locator"] == "i0"
    assert (a["rate_basis"], a["negotiated_rate"]) == ("dollar_from_percent", 682.66)   # 58% x 1177 = 682.66
    assert (a["median_amount_raw"], a["median_amount"], a["allowed_amounts_suppressed"]) == (25805.1, None, "count_zero")
    assert json.loads(a["unmapped_json"]) == {"standard_charges.vendor_extra": 1}
    assert a2["negotiated_rate"] == 12.5 and k[("numeric_encoded_as_string", "standard_charge_dollar")] == 1
    assert (b["source_locator"], b["rate_basis"]) == ("i1.s0.p-", "no_payer")
    assert k[("unmapped_field", "standard_charges.vendor_extra")] == 1   # logged once per object; kept on every row
    assert k[("unmapped_meta_column", "extra_top")] == 1
    assert k[("code_cpt_declared_not_cpt", "code_type")] == 1
    assert k[("item_unmappable", "standard_charge_information")] == 1
    assert p.meta.license_state == "IL"
    fams = {c["code"]: c["code_family"] for c in out["code"]}
    assert fams == {"0232T": "CPT_CAT_III", "A4216": "HCPCS_II"}


def test_top_level_order_does_not_matter():
    """UChicago puts modifier_information before the charges; NM puts it after; meta may even come last."""
    items = [{"description": "A", "code_information": [{"code": "1", "type": "RC"}],
              "standard_charges": [{"setting": "both", "gross_charge": 1.0}]}]
    doc = json.loads(_doc(items))
    reordered = {"standard_charge_information": doc.pop("standard_charge_information")} | doc
    p, out = run(json.dumps(reordered).encode())
    assert p.meta.hospital_name == "H" and p.meta.type_2_npi == ["1234567893"] and len(out["charge"]) == 1
    assert not any(kind == "missing_required_meta" for kind, _ in kinds(p))


def test_missing_required_meta_is_logged():
    p, _ = run(json.dumps({"hospital_name": "H", "standard_charge_information": []}).encode())
    k = kinds(p)
    assert ("missing_required_meta", "version") in k and ("missing_required_meta", "type_2_npi") in k

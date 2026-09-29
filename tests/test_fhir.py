"""FHIR path: read-tracked mapping, synthetic-marker flags, reference resolution, structural issues, staging.

All bundles here are hand-built and synthetic (Synthea-style markers, 999-range SSNs, digit-suffixed names).
"""

import json
from pathlib import Path

import pyarrow.parquet as pq

from clear_pricer import fhir
from clear_pricer.cli import main

FIX = Path(__file__).parent / "fixtures"
SYNTHEA = "https://github.com/synthetichealth/synthea"


def patient(pid="p1", *, synthetic=True):
    ids = [{"system": SYNTHEA, "value": pid}] if synthetic else []
    ids.append({"system": "http://hl7.org/fhir/sid/us-ssn", "value": "999-12-3456" if synthetic else "123-45-6789"})
    return {"resourceType": "Patient", "id": pid, "identifier": ids, "gender": "female", "birthDate": "1990-01-01",
            "name": [{"family": "Doe123" if synthetic else "Doe", "given": ["Jane45" if synthetic else "Jane"]}],
            "address": [{"city": "Chicago", "state": "IL", "postalCode": "60637", "line": ["1 Test St"]}]}


def bundle(*resources, btype="transaction"):
    return {"resourceType": "Bundle", "type": btype,
            "entry": [{"fullUrl": f"urn:uuid:{r.get('id')}", "resource": r} for r in resources]}


def enc(eid="e1", pid="p1", org="org1"):
    return {"resourceType": "Encounter", "id": eid, "status": "finished", "class": {"code": "AMB"},
            "type": [{"coding": [{"system": "http://snomed.info/sct", "code": "185349003"}]}],
            "subject": {"reference": f"urn:uuid:{pid}"}, "period": {"start": "2020-01-01T10:00:00+00:00"},
            "serviceProvider": {"reference": f"Organization?identifier={SYNTHEA}|{org}"}}


def org(oid="org1"):
    return {"resourceType": "Organization", "id": oid, "name": "TEST CLINIC",
            "identifier": [{"system": SYNTHEA, "value": oid}]}


def parse(*bundles):
    stage = fhir.FhirStage()
    for b in bundles:
        fhir.parse_bundle(stage, json.dumps(b).encode())
    fhir.resolve_references(stage)
    return stage


def test_synthetic_markers_are_flagged_and_values_not_staged():
    s = parse(bundle(patient("p1"), patient("p2", synthetic=False)))
    flags = {p["resource_id"]: (p["has_synthea_identifier"], p["ssn_in_999_range"], p["names_digit_suffixed"])
             for p in s.rows["patients"]}
    assert flags == {"p1": (True, True, True), "p2": (False, False, False)}
    staged = json.dumps(s.rows["patients"])
    assert "123-45-6789" not in staged and "Jane" not in staged and "1 Test St" not in staged


def test_references_resolve_by_kind():
    claim = {"resourceType": "Claim", "id": "c1", "status": "active", "type": {"coding": [{"system": "x", "code": "y"}]},
             "use": "claim", "patient": {"reference": "urn:uuid:p1"}, "created": "2020-01-01",
             "provider": {"reference": f"Organization?identifier={SYNTHEA}|org1"}, "priority": {}, "insurance": [],
             "contained": [{"resourceType": "Coverage", "id": "coverage"}],
             "prescription": {"reference": "#coverage"}, "referral": {"reference": "#missing"},
             "item": [{"sequence": 1, "productOrService": {"coding": [{"system": "http://www.ada.org/cdt",
                                                                        "code": "D0120"}]}}]}
    s = parse(bundle(patient(), enc(), claim, {"resourceType": "Observation", "id": "o1", "status": "final",
                                                "code": {"coding": [{"system": "http://loinc.org", "code": "1-1"}]},
                                                "subject": {"reference": "urn:uuid:nobody"}}),
              bundle(org(), btype="batch"))
    got = {(r["resource_id"], r["target"]): (r["kind"], r["resolved"]) for r in s.rows["references"]}
    assert got[("e1", "urn:uuid:p1")] == ("bundle_urn", True)
    assert got[("e1", f"Organization?identifier={SYNTHEA}|org1")] == ("conditional_identifier", True)
    assert got[("c1", "#coverage")] == ("contained", True)
    assert got[("c1", "#missing")] == ("contained", False)
    assert got[("o1", "urn:uuid:nobody")] == ("bundle_urn", False)
    assert s.rows["claim_items"][0]["code"] == "D0120"


def test_structural_issues_are_recorded_not_dropped():
    bad_obs = {"resourceType": "Observation", "id": "o1", "code": {"coding": [{"code": "no-system"}]},
               "effectiveDateTime": "yesterday"}
    s = parse(bundle(bad_obs, {"resourceType": "Encounter", "status": "finished", "class": {}}))
    kinds = {(i["resource_type"], i["kind"], i["detail"]) for i in s.rows["issues"]}
    assert ("Observation", "missing_required", "status") in kinds
    assert ("Observation", "coding_without_system_or_code", "code.coding[]") in kinds
    assert ("Observation", "unparseable_date", "effectiveDateTime=yesterday") in kinds
    assert ("Encounter", "missing_id", "entry 1") in kinds
    assert len(s.rows["observations"]) == 1  # still mapped


def test_mapping_is_measured_by_reads():
    p = patient()
    p["maritalStatus"] = {"text": "never read"}
    s = parse(bundle(p))
    m = {(r["resource_type"], r["path"]): r["mapped"] for r in fhir.mapping_report(s)}
    assert m[("Patient", "gender")] is True
    assert m[("Patient", "identifier[].system")] is True
    assert m[("Patient", "maritalStatus.text")] is False
    assert m[("Patient", "address[].line[]")] is False  # present, deliberately not read


def test_stage_dir_writes_every_table_even_without_bundles(tmp_path):
    counts = fhir.stage_dir(tmp_path / "no-such-dir", tmp_path / "out", None)
    assert all(v == 0 for v in counts.values())
    for name in counts:
        assert pq.read_table(tmp_path / "out" / f"{name}.parquet").num_rows == 0


def _write_bundles(data: Path, *bundles):
    d = data / "raw" / "synthea" / "output" / "fhir"
    d.mkdir(parents=True)
    for i, b in enumerate(bundles):
        (d / f"b{i}.json").write_text(json.dumps(b), encoding="utf-8")


def _run(data: Path) -> int:
    assert main(["--data-dir", str(data), "stage", "rush", "--source-file", str(FIX / "rush_sample.csv")]) == 0
    assert main(["--data-dir", str(data), "fhir-stage"]) == 0
    return main(["--data-dir", str(data), "build"])


def test_gate_passes_on_synthetic_data(tmp_path):
    _write_bundles(tmp_path / "data", bundle(patient(), enc()), bundle(org(), btype="batch"))
    assert _run(tmp_path / "data") == 0


def test_gate_fails_on_a_non_synthetic_patient(tmp_path):
    """Design pin 1: one real-looking patient must turn the run red."""
    _write_bundles(tmp_path / "data", bundle(patient(synthetic=False), enc()), bundle(org(), btype="batch"))
    assert _run(tmp_path / "data") == 1


def test_gate_fails_on_a_dangling_reference(tmp_path):
    _write_bundles(tmp_path / "data", bundle(patient(), enc(org="not-in-directory")), bundle(org(), btype="batch"))
    assert _run(tmp_path / "data") == 1

"""Synthetic FHIR R4 (Synthea) ingestion: parse, map, validate, and report what was not mapped.

No PHI, by construction (design pin 1): every bundle is Synthea output, and a gate checks each Patient carries
Synthea's markers (its identifier system, an SSN in the never-issued 999 range, digit-suffixed names). Even so,
the staged tables keep only *flags* for those fields -- never names, SSNs, or addresses below city/ZIP -- because
minimising PHI-shaped fields is the habit worth demonstrating.

"Mapped" is measured, not declared: each resource is wrapped in a `Tracked` view that records every leaf the
extractors actually read. Leaves present in the data but never read are reported per resource type as unmapped
(design pin 3), so the mapping report cannot drift from the code.

Validation is structural (FHIR R4 cardinality for the fields used, coding system+code, parseable dates) plus
reference integrity: every `urn:uuid:` reference must resolve within its bundle, and every conditional reference
(`Organization?identifier=...`) must resolve against the hospital/practitioner bundles.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# --- read tracking -------------------------------------------------------------------------------------------------


class Tracked:
    """Read-only view over parsed JSON that records which leaf paths were read (arrays collapse to `[]`)."""

    def __init__(self, value: object, path: str, reads: set[str]):
        self._v, self._p, self._reads = value, path, reads

    def get(self, key: str, default: object = None) -> object:
        if not isinstance(self._v, dict) or key not in self._v:
            return default
        return self._wrap(self._v[key], f"{self._p}.{key}" if self._p else key)

    def items(self) -> Iterator["Tracked"]:
        """Iterate a JSON array (as Tracked children)."""
        if isinstance(self._v, list):
            for x in self._v:
                yield self._wrap(x, f"{self._p}[]")

    def first(self) -> "Tracked | None":
        return next(self.items(), None)

    def _wrap(self, v: object, p: str) -> object:
        if isinstance(v, (dict, list)):
            return Tracked(v, p, self._reads)
        self._reads.add(p)
        return v

    def __bool__(self) -> bool:
        return bool(self._v)


def leaf_paths(value: object, path: str = "") -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield from leaf_paths(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for v in value:
            yield from leaf_paths(v, f"{path}[]")
    else:
        yield path


# --- small extractors ----------------------------------------------------------------------------------------------

def coding(cc: object) -> tuple[str | None, str | None, str | None]:
    """First coding of a CodeableConcept: (system, code, display)."""
    if not isinstance(cc, Tracked):
        return None, None, None
    c = cc.get("coding")
    first = c.first() if isinstance(c, Tracked) else None
    if first is None:
        return None, None, None
    return first.get("system"), first.get("code"), first.get("display")


def ref(r: object) -> str | None:
    return r.get("reference") if isinstance(r, Tracked) else None


def urn_id(reference: str | None) -> str | None:
    return reference[9:] if reference and reference.startswith("urn:uuid:") else None


# --- per-resource mappers ------------------------------------------------------------------------------------------

_DIGITS = re.compile(r"\d")


def _patient(r: Tracked) -> dict:
    ids = [(i.get("system"), i.get("value")) for i in r.get("identifier").items()] if r.get("identifier") else []
    names = r.get("name")
    name_parts = []
    if names:
        for n in names.items():
            name_parts.append(n.get("family") or "")
            given = n.get("given")
            name_parts += [g for g in given.items()] if isinstance(given, Tracked) else []
    ssn = next((v for s, v in ids if s == "http://hl7.org/fhir/sid/us-ssn"), None)
    addr = r.get("address").first() if r.get("address") else None
    race = eth = None
    for ext in (r.get("extension").items() if r.get("extension") else []):
        url = ext.get("url")
        if url in ("http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
                   "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity"):
            text = next((e.get("valueString") for e in ext.get("extension").items() if e.get("url") == "text"), None)
            if url.endswith("race"):
                race = text
            else:
                eth = text
    return {
        "gender": r.get("gender"), "birth_date": r.get("birthDate"), "deceased_at": r.get("deceasedDateTime"),
        "city": addr.get("city") if addr else None, "state": addr.get("state") if addr else None,
        "postal_code": addr.get("postalCode") if addr else None, "race": race, "ethnicity": eth,
        # synthetic-by-construction markers (flags only; the values themselves are not staged)
        "has_synthea_identifier": any(s == "https://github.com/synthetichealth/synthea" for s, _ in ids),
        "ssn_in_999_range": None if ssn is None else ssn.startswith("999"),
        "names_digit_suffixed": bool(name_parts) and all(bool(_DIGITS.search(p)) for p in name_parts if p),
    }


def _encounter(r: Tracked) -> dict:
    sysc, code, disp = coding(r.get("type").first() if r.get("type") else None)
    period = r.get("period")
    reason = coding(r.get("reasonCode").first() if r.get("reasonCode") else None)
    participant = r.get("participant").first() if r.get("participant") else None
    return {"patient_id": urn_id(ref(r.get("subject"))), "status": r.get("status"),
            "class_code": r.get("class").get("code") if r.get("class") else None,
            "type_system": sysc, "type_code": code, "type_display": disp,
            "start": period.get("start") if period else None, "end": period.get("end") if period else None,
            "reason_system": reason[0], "reason_code": reason[1],
            "provider_ref": ref(r.get("serviceProvider")),
            "practitioner_ref": ref(participant.get("individual")) if participant else None}


def _clinical(r: Tracked, time_key: str) -> dict:
    s, c, d = coding(r.get("code"))
    return {"patient_id": urn_id(ref(r.get("subject"))), "encounter_id": urn_id(ref(r.get("encounter"))),
            "status": r.get("status") if r.get("status") is not None else
            (coding(r.get("clinicalStatus"))[1] if r.get("clinicalStatus") else None),
            "code_system": s, "code": c, "display": d, "time": r.get(time_key)}


def _observation(r: Tracked) -> dict:
    row = _clinical(r, "effectiveDateTime")
    q = r.get("valueQuantity")
    cat = coding(r.get("category").first() if r.get("category") else None)
    vcc = coding(r.get("valueCodeableConcept")) if r.get("valueCodeableConcept") else (None, None, None)
    return row | {"category": cat[1], "value_number": q.get("value") if q else None,
                  "value_unit": q.get("unit") if q else None, "value_code": vcc[1]}


def _procedure(r: Tracked) -> dict:
    row = _clinical(r, "performedDateTime")
    p = r.get("performedPeriod")
    return row | {"time": row["time"] or (p.get("start") if p else None)}


def _medication_request(r: Tracked) -> dict:
    s, c, d = coding(r.get("medicationCodeableConcept"))
    return {"patient_id": urn_id(ref(r.get("subject"))), "encounter_id": urn_id(ref(r.get("encounter"))),
            "status": r.get("status"), "intent": r.get("intent"), "code_system": s, "code": c, "display": d,
            "time": r.get("authoredOn")}


def _claim(r: Tracked) -> dict:
    s, c, _ = coding(r.get("type"))
    period, total = r.get("billablePeriod"), r.get("total")
    return {"patient_id": urn_id(ref(r.get("patient"))), "status": r.get("status"), "use": r.get("use"),
            "claim_type": c, "start": period.get("start") if period else None,
            "created": r.get("created"), "provider_ref": ref(r.get("provider")),
            "total": total.get("value") if total else None, "currency": total.get("currency") if total else None}


def _claim_items(r: Tracked, claim_id: str) -> Iterator[dict]:
    for it in (r.get("item").items() if r.get("item") else []):
        s, c, d = coding(it.get("productOrService"))
        net = it.get("net")
        yield {"claim_id": claim_id, "sequence": it.get("sequence"), "code_system": s, "code": c, "display": d,
               "net": net.get("value") if net else None}


def _eob(r: Tracked) -> dict:
    payment = r.get("payment")
    total = r.get("total").first() if r.get("total") else None
    return {"patient_id": urn_id(ref(r.get("patient"))), "status": r.get("status"),
            "claim_id": urn_id(ref(r.get("claim"))), "outcome": r.get("outcome"),
            "payment": payment.get("amount").get("value") if payment and payment.get("amount") else None,
            "total_amount": total.get("amount").get("value") if total and total.get("amount") else None}


def _org(r: Tracked) -> dict:
    ids = [(i.get("system"), i.get("value")) for i in r.get("identifier").items()] if r.get("identifier") else []
    addr = r.get("address").first() if r.get("address") else None
    t = coding(r.get("type").first() if r.get("type") else None)
    name = r.get("name") if r.get("resourceType") != "Practitioner" else None  # person names are not staged
    return {"name": name if isinstance(name, str) else None, "synthea_id": next((v for s, v in ids if s and "synthea" in s), None),
            "npi": next((v for s, v in ids if s == "http://hl7.org/fhir/sid/us-npi"), None), "type_code": t[1],
            "city": addr.get("city") if addr else None, "state": addr.get("state") if addr else None,
            "postal_code": addr.get("postalCode") if addr else None}


MAPPERS = {"Patient": _patient, "Encounter": _encounter, "Condition": lambda r: _clinical(r, "onsetDateTime"),
           "Observation": _observation, "Procedure": _procedure, "MedicationRequest": _medication_request,
           "Claim": _claim, "ExplanationOfBenefit": _eob, "Organization": _org, "Location": _org,
           "Practitioner": _org}

# FHIR R4 1..1 elements (for the fields this project uses). Absent = a structural issue, recorded not dropped.
REQUIRED = {
    "Encounter": ("status", "class"), "Condition": ("subject",), "Observation": ("status", "code"),
    "Procedure": ("status", "subject"), "MedicationRequest": ("status", "intent", "subject"),
    "Claim": ("status", "type", "use", "patient", "created", "provider", "priority", "insurance"),
    "ExplanationOfBenefit": ("status", "type", "use", "patient", "created", "insurer", "provider", "outcome",
                             "insurance"),
    "Immunization": ("status", "vaccineCode", "patient"), "DiagnosticReport": ("status", "code"),
}

_DATE = re.compile(r"\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?)?)?")


# --- bundle parsing ------------------------------------------------------------------------------------------------

@dataclass
class FhirStage:
    rows: dict[str, list[dict]] = field(default_factory=lambda: {k: [] for k in (
        "resources", "patients", "encounters", "conditions", "observations", "procedures", "medication_requests",
        "claims", "claim_items", "eobs", "organizations", "references", "issues")})
    leaves: Counter = field(default_factory=Counter)      # (resource_type, path) -> occurrences
    reads: dict[str, set[str]] = field(default_factory=dict)  # resource_type -> paths read
    bundles: list[dict] = field(default_factory=list)

    def issue(self, bundle: str, rtype: str, rid: str | None, kind: str, detail: str = "") -> None:
        self.rows["issues"].append({"bundle_sha256": bundle, "resource_type": rtype, "resource_id": rid,
                                    "kind": kind, "detail": detail[:200]})


TABLE_FOR = {"Patient": "patients", "Encounter": "encounters", "Condition": "conditions",
             "Observation": "observations", "Procedure": "procedures", "MedicationRequest": "medication_requests",
             "Claim": "claims", "ExplanationOfBenefit": "eobs", "Organization": "organizations",
             "Location": "organizations", "Practitioner": "organizations"}

_REF_KEYS = ("reference",)


def _collect_refs(value: object, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else k
            if k in _REF_KEYS and isinstance(v, str):
                yield path, v
            else:
                yield from _collect_refs(v, p)
    elif isinstance(value, list):
        for v in value:
            yield from _collect_refs(v, f"{path}[]")


def parse_bundle(stage: FhirStage, raw: bytes) -> None:
    sha = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw.decode("utf-8-sig"))
    if doc.get("resourceType") != "Bundle":
        stage.issue(sha, str(doc.get("resourceType")), None, "not_a_bundle")
        return
    entries = doc.get("entry") or []
    kinds = Counter(e.get("resource", {}).get("resourceType") for e in entries)
    stage.bundles.append({"bundle_sha256": sha, "bundle_type": doc.get("type"), "entries": len(entries),
                          "patients": kinds.get("Patient", 0)})
    for n, e in enumerate(entries):
        res = e.get("resource")
        if not isinstance(res, dict) or "resourceType" not in res:
            stage.issue(sha, "?", None, "entry_without_resource", f"entry {n}")
            continue
        rtype, rid = res["resourceType"], res.get("id")
        if not rid:
            stage.issue(sha, rtype, None, "missing_id", f"entry {n}")
        full_url = e.get("fullUrl")
        stage.rows["resources"].append({"bundle_sha256": sha, "entry": n, "resource_type": rtype,
                                        "resource_id": rid, "full_url": full_url})
        for p in leaf_paths(res):
            stage.leaves[(rtype, p)] += 1
        contained = {f"#{c.get('id')}" for c in res.get("contained") or [] if isinstance(c, dict) and c.get("id")}
        for path, target in _collect_refs(res):
            row = {"bundle_sha256": sha, "resource_type": rtype, "resource_id": rid, "path": path, "target": target}
            if target.startswith("#"):  # a reference to a resource contained inside this one
                row |= {"kind": "contained", "resolved": target in contained}
            stage.rows["references"].append(row)
        for req in REQUIRED.get(rtype, ()):
            if req not in res:
                stage.issue(sha, rtype, rid, "missing_required", req)
        _check_codings(stage, sha, rtype, rid, res)
        _check_dates(stage, sha, rtype, rid, res)

        mapper = MAPPERS.get(rtype)
        if mapper:
            reads = stage.reads.setdefault(rtype, set())
            t = Tracked(res, "", reads)
            t.get("resourceType"), t.get("id")
            row = {"bundle_sha256": sha, "resource_id": rid} | mapper(t)
            if rtype in ("Organization", "Location", "Practitioner"):
                row["resource_type"] = rtype
            stage.rows[TABLE_FOR[rtype]].append(row)
            if rtype == "Claim":
                stage.rows["claim_items"].extend(_claim_items(t, rid))


def _check_codings(stage: FhirStage, sha: str, rtype: str, rid: str | None, value: object, path: str = "") -> None:
    if isinstance(value, dict):
        if path.endswith("coding[]") and not (value.get("system") and value.get("code")):
            stage.issue(sha, rtype, rid, "coding_without_system_or_code", path)
        for k, v in value.items():
            _check_codings(stage, sha, rtype, rid, v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for v in value:
            _check_codings(stage, sha, rtype, rid, v, f"{path}[]")


_DATE_KEYS = ("birthDate", "deceasedDateTime", "start", "end", "onsetDateTime", "abatementDateTime",
              "effectiveDateTime", "issued", "performedDateTime", "authoredOn", "created", "recorded", "date",
              "occurrenceDateTime")


def _check_dates(stage: FhirStage, sha: str, rtype: str, rid: str | None, value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else k
            if k in _DATE_KEYS and isinstance(v, str) and not _DATE.fullmatch(v):
                stage.issue(sha, rtype, rid, "unparseable_date", f"{p}={v}")
            else:
                _check_dates(stage, sha, rtype, rid, v, p)
    elif isinstance(value, list):
        for v in value:
            _check_dates(stage, sha, rtype, rid, v, f"{path}[]")


def resolve_references(stage: FhirStage) -> None:
    """Mark each reference resolved: urn:uuid within its bundle; conditional against the directory bundles;
    `#id` against the resource's own `contained` (set during parsing)."""
    by_bundle: dict[str, set[str]] = {}
    for r in stage.rows["resources"]:
        if r["full_url"]:
            by_bundle.setdefault(r["bundle_sha256"], set()).add(r["full_url"])
    identifiers: set[tuple[str, str, str]] = set()
    for row in stage.rows["organizations"]:
        if row.get("synthea_id"):
            identifiers.add((row["resource_type"], "https://github.com/synthetichealth/synthea", row["synthea_id"]))
        if row.get("npi"):
            identifiers.add((row["resource_type"], "http://hl7.org/fhir/sid/us-npi", row["npi"]))
    cond = re.compile(r"([A-Za-z]+)\?identifier=([^|]+)\|(.+)")
    for r in stage.rows["references"]:
        t = r["target"]
        if r.get("kind") == "contained":
            continue
        if t.startswith("urn:uuid:"):
            r["kind"], r["resolved"] = "bundle_urn", t in by_bundle.get(r["bundle_sha256"], set())
        elif m := cond.fullmatch(t):
            r["kind"], r["resolved"] = "conditional_identifier", (m.group(1), m.group(2), m.group(3)) in identifiers
        else:
            r["kind"], r["resolved"] = "other", False


def mapping_report(stage: FhirStage) -> list[dict]:
    rows = []
    for (rtype, path), n in sorted(stage.leaves.items()):
        mapped = path in stage.reads.get(rtype, set())
        rows.append({"resource_type": rtype, "path": path, "occurrences": n, "mapped": mapped,
                     "modelled_type": rtype in MAPPERS})
    return rows


def parse_dir(fhir_dir: Path) -> FhirStage:
    """Parse every bundle, in content-hash order (Synthea's directory-file names carry a wall-clock timestamp)."""
    stage = FhirStage()
    order = sorted((hashlib.sha256(p.read_bytes()).hexdigest(), p) for p in fhir_dir.glob("*.json"))
    for _, path in order:  # one bundle in memory at a time
        parse_bundle(stage, path.read_bytes())
    resolve_references(stage)
    return stage


# --- staging -------------------------------------------------------------------------------------------------------

def _schemas() -> dict:
    import pyarrow as pa

    S, F, I, B = pa.string(), pa.float64(), pa.int64(), pa.bool_()
    base = [("bundle_sha256", S), ("resource_id", S)]
    clinical = base + [("patient_id", S), ("encounter_id", S), ("status", S), ("code_system", S), ("code", S),
                       ("display", S), ("time", S)]
    return {
        "bundles": [("bundle_sha256", S), ("bundle_type", S), ("entries", I), ("patients", I)],
        "resources": [("bundle_sha256", S), ("entry", I), ("resource_type", S), ("resource_id", S), ("full_url", S)],
        "patients": base + [("gender", S), ("birth_date", S), ("deceased_at", S), ("city", S), ("state", S),
                            ("postal_code", S), ("race", S), ("ethnicity", S), ("has_synthea_identifier", B),
                            ("ssn_in_999_range", B), ("names_digit_suffixed", B)],
        "encounters": base + [("patient_id", S), ("status", S), ("class_code", S), ("type_system", S),
                              ("type_code", S), ("type_display", S), ("start", S), ("end", S), ("reason_system", S),
                              ("reason_code", S), ("provider_ref", S), ("practitioner_ref", S)],
        "conditions": clinical,
        "observations": clinical + [("category", S), ("value_number", F), ("value_unit", S), ("value_code", S)],
        "procedures": clinical,
        "medication_requests": base + [("patient_id", S), ("encounter_id", S), ("status", S), ("intent", S),
                                       ("code_system", S), ("code", S), ("display", S), ("time", S)],
        "claims": base + [("patient_id", S), ("status", S), ("use", S), ("claim_type", S), ("start", S),
                          ("created", S), ("provider_ref", S), ("total", F), ("currency", S)],
        "claim_items": [("claim_id", S), ("sequence", I), ("code_system", S), ("code", S), ("display", S),
                        ("net", F)],
        "eobs": base + [("patient_id", S), ("status", S), ("claim_id", S), ("outcome", S), ("payment", F),
                        ("total_amount", F)],
        "organizations": base + [("resource_type", S), ("name", S), ("synthea_id", S), ("npi", S), ("type_code", S),
                                 ("city", S), ("state", S), ("postal_code", S)],
        "references": [("bundle_sha256", S), ("resource_type", S), ("resource_id", S), ("path", S), ("target", S),
                       ("kind", S), ("resolved", B)],
        "issues": [("bundle_sha256", S), ("resource_type", S), ("resource_id", S), ("kind", S), ("detail", S)],
        "mapping": [("resource_type", S), ("path", S), ("occurrences", I), ("mapped", B), ("modelled_type", B)],
        "run": [("synthea_version", S), ("jar_sha256", S), ("args", S), ("bundles", I), ("resources", I)],
    }


def stage_dir(fhir_dir: Path, out_dir: Path, manifest: dict | None) -> dict:
    """Parse all bundles (if any) and write one Parquet per table -- always, so dbt builds on a clean clone too."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    stage = parse_dir(fhir_dir) if fhir_dir.exists() else FhirStage()
    tables = dict(stage.rows) | {"bundles": stage.bundles, "mapping": mapping_report(stage)}
    tables["run"] = [{"synthea_version": manifest.get("synthea_version"), "jar_sha256": manifest.get("jar_sha256"),
                      "args": " ".join(manifest.get("args", [])), "bundles": len(stage.bundles),
                      "resources": len(stage.rows["resources"])}] if manifest else []
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, cols in _schemas().items():
        rows = [{c: r.get(c) for c, _ in cols} for r in tables.get(name, [])]
        pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema(cols)), out_dir / f"{name}.parquet",
                       compression="zstd")
    return {name: len(tables.get(name, [])) for name in _schemas()}


"""Streaming parser for CMS HPT v3 JSON machine-readable files.

Pure: takes a binary file-like object (positioned after any BOM) and streams normalised rows. One pass with ijson;
only one `standard_charge_information` item is materialised at a time, so a 5 GB file (Northwestern) parses in
bounded memory. Top-level keys may appear in any order (UChicago puts `modifier_information` before the charges, NM
after), so header metadata is complete only once `events()` is exhausted.

Grain: one charge row per payers_information entry (or one `no_payer` row for a standard_charges entry with no
payers). Locators: item "i<n>", charge "i<n>.s<k>.p<j>" (p- for no payer). Unknown keys are kept in
`unmapped_json` and logged as `unmapped_field` (design pin 3).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import BinaryIO

import ijson
from ijson.common import ObjectBuilder

from clear_pricer.normalise import DriftLog, Meta, charge_row, check_positive, code_rows

SCI = "standard_charge_information"
TOP_KNOWN = {"hospital_name", "last_updated_on", "version", "location_name", "hospital_address", "type_2_npi",
             "license_information", "attestation", SCI, "modifier_information",
             "financial_aid_policy", "general_contract_provisions"}
ITEM_KNOWN = {"description", "drug_information", "code_information", "standard_charges"}
SC_KNOWN = {"setting", "gross_charge", "discounted_cash", "minimum", "maximum", "modifier_code", "payers_information",
            "additional_generic_notes", "billing_class"}  # billing_class: optional in the v3 dictionary
_VALUE_END = frozenset({"end_map", "end_array", "string", "number", "boolean", "null"})
PAYER_KNOWN = {"payer_name", "plan_name", "additional_payer_notes", "standard_charge_dollar",
               "standard_charge_algorithm", "standard_charge_percentage", "median_amount", "10th_percentile",
               "90th_percentile", "count", "methodology"}


class JsonParse:
    def __init__(self, fp: BinaryIO):
        self.fp = fp
        self.meta = Meta()
        self.drift = DriftLog()
        self.records_read = 0
        self._top_seen: set[str] = set()

    # --- streaming --------------------------------------------------------------------------------------------

    def events(self) -> Iterator[tuple[str, dict]]:
        item_no = 0
        builder: ObjectBuilder | None = None
        top_key: str | None = None
        top_builder: ObjectBuilder | None = None
        item_prefix = f"{SCI}.item"
        for prefix, event, value in ijson.parse(self.fp, use_float=True):
            if prefix == "":
                if event == "map_key":
                    top_key = value
                    self._top_seen.add(value)
                elif event not in ("start_map", "end_map"):
                    self.drift.add("top_level_not_object", sample=event)
                continue
            if top_key == SCI:
                if prefix == SCI:
                    if event not in ("start_array", "end_array"):
                        self.drift.add("sci_not_array", SCI, event)
                    continue
                if prefix == item_prefix and builder is None:
                    builder = ObjectBuilder()
                builder.event(event, value)
                if prefix == item_prefix and event in _VALUE_END:
                    item, builder = builder.value, None
                    yield from self._item(item_no, item)
                    item_no += 1
                continue
            # any other top-level key: build its (small) value, then handle it. (map_key events inside the value
            # share the value's prefix, so only end_* / scalar events at that prefix close it.)
            if prefix == top_key and top_builder is None:
                top_builder = ObjectBuilder()
            top_builder.event(event, value)
            if prefix == top_key and event in _VALUE_END:
                yield from self._top(top_key, top_builder.value)
                top_builder = None
        self._finish_meta()

    # --- top-level metadata + modifiers --------------------------------------------------------------------------

    def _top(self, key: str, value: object) -> Iterator[tuple[str, dict]]:
        m = self.meta
        as_list = lambda v: [str(x).strip() for x in v] if isinstance(v, list) else ([str(v).strip()] if v else [])  # noqa: E731
        if key == "hospital_name":
            m.hospital_name = value
        elif key == "last_updated_on":
            m.last_updated_on = value
        elif key == "version":
            m.version = value
        elif key == "location_name":
            m.location_name = as_list(value)
        elif key == "hospital_address":
            m.hospital_address = as_list(value)
        elif key == "type_2_npi":
            m.type_2_npi = as_list(value)
        elif key == "license_information" and isinstance(value, dict):
            m.license_number = value.get("license_number") or None
            m.license_state = (value.get("state") or "").upper() or None
        elif key == "attestation" and isinstance(value, dict):
            confirm = value.get("confirm_attestation")
            m.attestation = None if confirm is None else str(confirm).lower()
            m.attester_name = value.get("attester_name")
        elif key == "modifier_information":
            yield from self._modifiers(value)
        elif key in TOP_KNOWN:
            m.unmapped[key] = value  # optional spec elements: kept, not modelled yet
        else:
            m.unmapped[key] = value
            self.drift.add("unmapped_meta_column", key, json.dumps(value, default=str)[:200])

    def _modifiers(self, value: object) -> Iterator[tuple[str, dict]]:
        if not isinstance(value, list):
            self.drift.add("modifier_information_not_array")
            return
        for i, mod in enumerate(value):
            for j, p in enumerate(mod.get("modifier_payer_information") or [{}]):
                yield "modifier", {"modifier_locator": f"m{i}.p{j}", "code": mod.get("code"),
                                   "description": mod.get("description"), "setting": mod.get("setting"),
                                   "payer_name": p.get("payer_name"), "plan_name": p.get("plan_name"),
                                   "payer_description": p.get("description")}

    def _finish_meta(self) -> None:
        if SCI not in self._top_seen:
            self.drift.add("missing_required_meta", SCI)
        self.meta.check_required(self.drift)

    # --- items ----------------------------------------------------------------------------------------------------

    def _unmapped(self, obj: dict, known: set[str], where: str, loc: str) -> dict:
        extra = {k: v for k, v in obj.items() if k not in known}
        for k, v in extra.items():
            self.drift.add("unmapped_field", f"{where}.{k}", json.dumps(v, default=str)[:200], loc)
        return extra

    def _num(self, obj: dict, key: str, loc: str) -> float | None:
        v = obj.get(key)
        if v is None or isinstance(v, bool):
            if isinstance(v, bool):
                self.drift.add("unparseable_numeric", key, v, loc)
            return None
        if isinstance(v, (int, float)):
            return check_positive(float(v), key, self.drift, loc)
        try:
            f = float(str(v).strip())
        except ValueError:
            self.drift.add("unparseable_numeric", key, v, loc)
            return None
        self.drift.add("numeric_encoded_as_string", key, v, loc)
        return check_positive(f, key, self.drift, loc)

    def _str(self, obj: dict, key: str) -> str | None:
        v = obj.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    def _item(self, n: int, item: object) -> Iterator[tuple[str, dict]]:
        iloc = f"i{n}"
        if not isinstance(item, dict) or not isinstance(item.get("standard_charges"), list) or not item["standard_charges"]:
            self.records_read += 1
            self.drift.add("item_unmappable", SCI, json.dumps(item, default=str)[:200], iloc)
            yield "quarantine", {"source_locator": iloc, "reason": "item_unmappable",
                                 "raw_json": json.dumps(item, ensure_ascii=False, default=str)}
            return
        for req in ("description", "code_information"):
            if req not in item:
                self.drift.add("missing_required_field", f"item.{req}", locator=iloc)
        item_extra = self._unmapped(item, ITEM_KNOWN, "item", iloc)
        drug = item.get("drug_information") or {}
        drug_unit = self._num(drug, "unit", iloc) if isinstance(drug, dict) else None
        drug_type = self._str(drug, "type") if isinstance(drug, dict) else None
        codes = item.get("code_information") or []
        pairs = [(i + 1, str(c.get("code") or ""), str(c.get("type") or "")) for i, c in enumerate(codes)
                 if isinstance(c, dict)]
        for row in code_rows(iloc, pairs, self.drift):
            yield "code", row

        for k, sc in enumerate(item["standard_charges"]):
            if not isinstance(sc, dict):
                self.records_read += 1
                self.drift.add("standard_charge_unmappable", "standard_charges", sc, f"{iloc}.s{k}")
                yield "quarantine", {"source_locator": f"{iloc}.s{k}", "reason": "standard_charge_unmappable",
                                     "raw_json": json.dumps(sc, ensure_ascii=False, default=str)}
                continue
            sc_extra = self._unmapped(sc, SC_KNOWN, "standard_charges", f"{iloc}.s{k}")
            mods = sc.get("modifier_code")
            modifiers = "|".join(map(str, mods)) if isinstance(mods, list) and mods else (mods or None)
            payers = sc.get("payers_information") or [None]
            for j, p in enumerate(payers):
                self.records_read += 1
                loc = f"{iloc}.s{k}.p{j}" if p is not None else f"{iloc}.s{k}.p-"
                p = p if isinstance(p, dict) else {}
                p_extra = self._unmapped(p, PAYER_KNOWN, "payers_information", loc)
                unmapped = ({f"item.{k2}": v for k2, v in item_extra.items()}
                            | {f"standard_charges.{k2}": v for k2, v in sc_extra.items()}
                            | {f"payers_information.{k2}": v for k2, v in p_extra.items()})
                count = p.get("count")
                yield "charge", charge_row(
                    locator=loc, item_locator=iloc, description=self._str(item, "description"),
                    setting=self._str(sc, "setting"), billing_class=self._str(sc, "billing_class"),
                    modifiers=modifiers, drug_unit=drug_unit, drug_type=drug_type,
                    gross=self._num(sc, "gross_charge", loc), cash=self._num(sc, "discounted_cash", loc),
                    payer_name=self._str(p, "payer_name"), plan_name=self._str(p, "plan_name"),
                    dollar=self._num(p, "standard_charge_dollar", loc),
                    percentage=self._num(p, "standard_charge_percentage", loc),
                    algorithm=self._str(p, "standard_charge_algorithm"), methodology=self._str(p, "methodology"),
                    median=self._num(p, "median_amount", loc), p10=self._num(p, "10th_percentile", loc),
                    p90=self._num(p, "90th_percentile", loc), count_raw=None if count is None else str(count),
                    minimum=self._num(sc, "minimum", loc), maximum=self._num(sc, "maximum", loc),
                    generic_notes=self._str(sc, "additional_generic_notes"),
                    payer_notes=self._str(p, "additional_payer_notes"), unmapped=unmapped, drift=self.drift,
                )

"""Parser for CMS HPT v3 CSV "tall" machine-readable files.

Pure: takes an iterable of text lines (no file or network I/O) and streams normalised rows; keeps a drift log of
everything it could not map (design pin 3: record, never silently drop):
- columns outside the v3 dictionary are kept per row in `unmapped_json` and logged as `unmapped_column`;
- values that fail their type/enum are nulled in the clean column, kept raw where a *_raw column exists, and logged;
- rows whose width does not match the header are quarantined (raw cells kept) and logged as `ragged_row`.

In CSV tall, each data row is its own item: item_locator == source_locator == "r<record number>".
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Iterator

from clear_pricer import rules
from clear_pricer.normalise import DriftLog, Meta, charge_row, check_positive, code_rows

ATTESTATION_PREFIX = "to the best of its knowledge and belief"
META_KNOWN = {"hospital_name", "last_updated_on", "version", "location_name", "hospital_address", "type_2_npi",
              "attester_name", "financial_aid_policy", "general_contract_provisions"}
# row-3 headers, v3.0 CSV tall (code|[i] and code|[i]|type handled separately)
DATA_REQUIRED = (
    "description", "setting", "modifiers", "drug_unit_of_measurement", "drug_type_of_measurement",
    "standard_charge|gross", "standard_charge|discounted_cash", "payer_name", "plan_name",
    "standard_charge|negotiated_dollar", "standard_charge|negotiated_percentage",
    "standard_charge|negotiated_algorithm", "median_amount", "10th_percentile", "90th_percentile", "count",
    "standard_charge|methodology", "standard_charge|min", "standard_charge|max", "additional_generic_notes",
)
DATA_OPTIONAL = ("billing_class",)
_CODE_COL = re.compile(r"code\|(\d+)")
_CODE_TYPE_COL = re.compile(r"code\|(\d+)\|type")


def norm_header(h: str) -> str:
    """Spec: headers are case-insensitive and spaces around pipes are tolerated."""
    return "|".join(part.strip() for part in h.strip().lower().split("|"))


def split_multi(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split("|") if v.strip()]


class CsvTallParse:
    """One parse of one file. Iterate `events()`; `meta`, `drift`, `records_read` are complete once it is exhausted."""

    def __init__(self, lines: Iterable[str]):
        self.lines = lines
        self.meta = Meta()
        self.drift = DriftLog()
        self.records_read = 0

    def events(self) -> Iterator[tuple[str, dict]]:
        reader = csv.reader(self.lines)
        try:
            meta_header = [norm_header(h) for h in next(reader)]
            meta_values = next(reader)
            header = [norm_header(h) for h in next(reader)]
        except StopIteration:
            self.drift.add("truncated_header")
            return
        self._parse_meta(meta_header, meta_values)
        index = self._map_header(header)

        for record_no, cells in enumerate(reader, start=4):  # file records 1-3 are headers
            loc = f"r{record_no}"
            if not any(c.strip() for c in cells):
                self.drift.add("blank_row", locator=loc)
                continue
            self.records_read += 1
            if len(cells) != len(header):
                self.drift.add("ragged_row", sample=f"{len(cells)} cells vs {len(header)}", locator=loc)
                yield "quarantine", {"source_locator": loc, "reason": "ragged_row",
                                     "raw_json": json.dumps(cells, ensure_ascii=False)}
                continue
            yield "charge", self._charge(loc, cells, index)
            pairs = [(seq, cells[c["code"]] if "code" in c else "", cells[c["type"]] if "type" in c else "")
                     for seq, c in sorted(index["codes"].items())]
            for row in code_rows(loc, pairs, self.drift):
                yield "code", row

    def _parse_meta(self, header: list[str], values: list[str]) -> None:
        m, d = self.meta, self.drift
        fields: dict[str, str | None] = {}
        for h, v in zip(header, values + [""] * (len(header) - len(values))):
            v = v.strip()
            if not h:
                if v:
                    d.add("meta_value_without_header", sample=v)
            elif h.startswith(ATTESTATION_PREFIX):
                m.attestation = v.lower() or None
            elif h.startswith("license_number"):
                m.license_state = h.split("|", 1)[1].upper() if "|" in h else None
                m.license_number = v or None
            elif h in META_KNOWN:
                fields[h] = v or None
            else:
                m.unmapped[h] = v
                d.add("unmapped_meta_column", h, v)
        m.hospital_name = fields.get("hospital_name")
        m.last_updated_on = fields.get("last_updated_on")
        m.version = fields.get("version")
        m.attester_name = fields.get("attester_name")
        m.location_name = split_multi(fields.get("location_name"))
        m.hospital_address = split_multi(fields.get("hospital_address"))
        m.type_2_npi = split_multi(fields.get("type_2_npi"))
        for extra in ("financial_aid_policy", "general_contract_provisions"):
            if fields.get(extra):
                m.unmapped[extra] = fields[extra]  # optional spec elements: kept, not modelled yet
        m.check_required(d)

    def _map_header(self, header: list[str]) -> dict:
        seen: dict[str, int] = {}
        codes: dict[int, dict[str, int]] = {}
        unmapped: dict[str, int] = {}
        for i, h in enumerate(header):
            if h in seen:
                self.drift.add("duplicate_column", h)
                continue
            seen[h] = i
            if m := _CODE_TYPE_COL.fullmatch(h):
                codes.setdefault(int(m.group(1)), {})["type"] = i
            elif m := _CODE_COL.fullmatch(h):
                codes.setdefault(int(m.group(1)), {})["code"] = i
            elif h not in DATA_REQUIRED and h not in DATA_OPTIONAL:
                unmapped[h] = i
                self.drift.add("unmapped_column", h)
        for req in DATA_REQUIRED:
            if req not in seen:
                self.drift.add("missing_required_column", req)
        if 1 not in codes:
            self.drift.add("missing_required_column", "code|1")
        return {"cols": seen, "codes": codes, "unmapped": unmapped}

    def _charge(self, loc: str, cells: list[str], index: dict) -> dict:
        cols = index["cols"]

        def s(col: str) -> str | None:
            i = cols.get(col)
            v = cells[i].strip() if i is not None else ""
            return v or None

        def n(col: str) -> float | None:
            raw = s(col)
            value, ok = rules.parse_number(raw)
            if not ok:
                self.drift.add("unparseable_numeric", col, raw, loc)
            return check_positive(value, col, self.drift, loc)

        return charge_row(
            locator=loc, item_locator=loc, description=s("description"), setting=s("setting"),
            billing_class=s("billing_class"), modifiers=s("modifiers"), drug_unit=n("drug_unit_of_measurement"),
            drug_type=s("drug_type_of_measurement"), gross=n("standard_charge|gross"),
            cash=n("standard_charge|discounted_cash"), payer_name=s("payer_name"), plan_name=s("plan_name"),
            dollar=n("standard_charge|negotiated_dollar"), percentage=n("standard_charge|negotiated_percentage"),
            algorithm=s("standard_charge|negotiated_algorithm"), methodology=s("standard_charge|methodology"),
            median=n("median_amount"), p10=n("10th_percentile"), p90=n("90th_percentile"), count_raw=s("count"),
            minimum=n("standard_charge|min"), maximum=n("standard_charge|max"),
            generic_notes=s("additional_generic_notes"), payer_notes=None,
            unmapped={h: cells[i] for h, i in index["unmapped"].items() if cells[i].strip()}, drift=self.drift,
        )

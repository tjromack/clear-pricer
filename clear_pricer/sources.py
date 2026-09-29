"""v1 hospital registry (CP-DEC 005). Discovery reads each site's cms-hpt.txt for `location_name`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hospital:
    id: str
    name: str
    ein: str
    site: str           # cms-hpt.txt lives at https://<site>/cms-hpt.txt
    location_name: str  # the cms-hpt.txt `location-name` to select
    format: str         # 'csv_tall' | 'json' (the parser to use)


HOSPITALS: dict[str, Hospital] = {h.id: h for h in (
    Hospital("rush", "Rush University Medical Center", "362174823", "www.rush.edu",
             "Rush University Medical Center", "csv_tall"),
    Hospital("uchicago", "University of Chicago Medical Center", "363488183", "www.uchicagomedicine.org",
             "University of Chicago Medical Center", "json"),
    Hospital("nm", "Northwestern Memorial Hospital", "370960170", "www.nm.org",
             "Northwestern Memorial Hospital", "json"),
)}

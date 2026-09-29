"""Parse CMS `cms-hpt.txt` discovery files. Pure: text in, entries out."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HptEntry:
    location_name: str
    source_page_url: str | None
    mrf_url: str
    fixes: tuple[str, ...]  # corrections applied (e.g. 'mrf_url_missing_scheme'); logged as drift by the caller


def normalise_url(url: str) -> tuple[str, bool]:
    """Returns (url, was_fixed). Scheme-less URLs (seen at Rush) get https://."""
    u = url.strip()
    if u.lower().startswith(("http://", "https://")):
        return u, False
    return "https://" + u.lstrip("/"), True


def parse_cms_hpt(text: str) -> list[HptEntry]:
    """Blank-line separated blocks of `key: value` lines."""
    entries: list[HptEntry] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        kv: dict[str, str] = {}
        for line in block.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                kv[k.strip().lower()] = v.strip()
        if "location-name" not in kv or "mrf-url" not in kv:
            continue
        fixes = []
        mrf, fixed = normalise_url(kv["mrf-url"])
        if fixed:
            fixes.append("mrf_url_missing_scheme")
        page = kv.get("source-page-url")
        if page:
            page, fixed = normalise_url(page)
            if fixed:
                fixes.append("source_page_url_missing_scheme")
        entries.append(HptEntry(kv["location-name"], page, mrf, tuple(fixes)))
    return entries


def find_location(entries: list[HptEntry], location_name: str) -> HptEntry | None:
    want = location_name.strip().casefold()
    return next((e for e in entries if e.location_name.strip().casefold() == want), None)

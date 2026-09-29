"""Landing: discover via cms-hpt.txt, download with curl, store content-addressed.

All HTTP goes through `curl` (OS trust store) because this machine's TLS-inspecting proxy breaks Python HTTPS
(CP-DEC 004). Landing is idempotent: a file is stored at raw/hpt/<hospital>/<sha256[:16]>/<filename>, so
re-fetching an unchanged file is a no-op, and a changed file lands beside the old one (never overwritten).
Change detection is by content hash, not HTTP headers (Rush sends neither Content-Length nor Last-Modified).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from clear_pricer.discovery import find_location, parse_cms_hpt
from clear_pricer.sources import Hospital


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class LandedFile:
    hospital_id: str
    path: str
    filename: str
    sha256: str
    bytes: int
    source_url: str | None
    fixes: tuple[str, ...]  # discovery corrections, surfaced as drift by staging


def _curl(url: str, out: Path, headers: Path | None = None) -> None:
    cmd = ["curl", "-sS", "-L", "--fail", "--retry", "3", "-A", "clear-pricer/0.1 (+github.com/tjromack/clear-pricer)",
           "-o", str(out), url]
    if headers is not None:
        cmd[1:1] = ["-D", str(headers)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FetchError(f"curl failed ({proc.returncode}) for {url}: {proc.stderr.strip()}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _filename(url: str, headers_text: str) -> str:
    # last Content-Disposition wins (redirect chains repeat headers)
    names = re.findall(r'content-disposition:.*?filename="?([^";\r\n]+)"?', headers_text, re.I)
    if names:
        return Path(names[-1].strip()).name
    return Path(unquote(urlparse(url).path)).name or "mrf"


def _land(hospital_id: str, tmp: Path, filename: str, url: str | None, fixes: tuple[str, ...],
          data_dir: Path) -> LandedFile:
    sha = _sha256(tmp)
    dest_dir = data_dir / "raw" / "hpt" / hospital_id / sha[:16]
    dest = dest_dir / filename
    if dest.exists():
        tmp.unlink()
    else:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), dest)
    landed = LandedFile(hospital_id, str(dest), filename, sha, dest.stat().st_size, url, fixes)
    manifest = dest_dir / "landing.json"
    if not manifest.exists():  # first-seen time is written once; re-runs don't rewrite it
        record = asdict(landed) | {"first_seen_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        manifest.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return landed


def fetch(h: Hospital, data_dir: Path) -> LandedFile:
    base = data_dir / "raw" / "hpt" / h.id
    incoming = base / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)

    hpt_path = base / "cms-hpt.txt"
    _curl(f"https://{h.site}/cms-hpt.txt", hpt_path)
    entry = find_location(parse_cms_hpt(hpt_path.read_text(encoding="utf-8-sig", errors="replace")), h.location_name)
    if entry is None:
        raise FetchError(f"{h.site}/cms-hpt.txt has no location-name {h.location_name!r}")

    tmp, hdr = incoming / "download.part", incoming / "headers.txt"
    _curl(entry.mrf_url, tmp, headers=hdr)
    filename = _filename(entry.mrf_url, hdr.read_text(encoding="latin-1"))
    return _land(h.id, tmp, filename, entry.mrf_url, entry.fixes, data_dir)


def land_local(hospital_id: str, source: Path, data_dir: Path) -> LandedFile:
    """Land a local file (offline mode / fixtures / deliberately broken inputs) exactly like a download."""
    incoming = data_dir / "raw" / "hpt" / hospital_id / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / "local.part"
    shutil.copyfile(source, tmp)
    return _land(hospital_id, tmp, source.name, None, (), data_dir)

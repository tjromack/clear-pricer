"""Landing: discover via cms-hpt.txt, download with curl, store content-addressed.

All HTTP goes through `curl` (OS trust store) because this machine's TLS-inspecting proxy breaks Python HTTPS
(CP-DEC 004). Landing is idempotent: a file is stored at raw/hpt/<hospital>/<sha256[:16]>/<filename>, so
re-fetching an unchanged file is a no-op, and a changed file lands beside the old one (never overwritten).
Change detection is by content hash, not HTTP headers (Rush sends neither Content-Length nor Last-Modified).
Where a server sends an ETag (NM, UChicago), the download is conditional: a 304 reuses the last landed file, so an
unchanged 5 GB file costs one request, not 5 GB. The hash stays the identity; the ETag only skips the transfer.
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


def _curl(url: str, out: Path, headers: Path | None = None, etag: Path | None = None, compare: bool = False) -> int:
    """Returns the final HTTP status. With `etag`, saves the response ETag there; with `compare`, sends it."""
    cmd = ["curl", "-sS", "-L", "--fail", "--retry", "3", "-A", "clear-pricer/0.1 (+github.com/tjromack/clear-pricer)",
           "-w", "%{http_code}", "-o", str(out)]
    if headers is not None:
        cmd += ["-D", str(headers)]
    if etag is not None:
        cmd += (["--etag-compare", str(etag)] if compare else []) + ["--etag-save", str(etag)]
    proc = subprocess.run(cmd + [url], capture_output=True, text=True)
    if proc.returncode != 0:
        raise FetchError(f"curl failed ({proc.returncode}) for {url}: {proc.stderr.strip()}")
    return int(proc.stdout.strip()[-3:] or 0)


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


def _portable(landed: LandedFile, data_dir: Path) -> dict:
    """Manifest form: path relative to data_dir, so host and container (different mount points) agree."""
    return asdict(landed) | {"path": Path(landed.path).relative_to(data_dir).as_posix()}


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
        record = _portable(landed, data_dir) | {"first_seen_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
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

    tmp, hdr, etag, latest = (incoming / "download.part", incoming / "headers.txt", base / "etag.txt",
                              base / "latest.json")
    prev = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else None
    if prev:
        prev["path"] = str(data_dir / prev["path"])
    can_compare = bool(prev and prev["source_url"] == entry.mrf_url and Path(prev["path"]).exists()
                       and etag.exists() and etag.read_text().strip())
    status = _curl(entry.mrf_url, tmp, headers=hdr, etag=etag, compare=can_compare)
    if status == 304 and can_compare:
        tmp.unlink(missing_ok=True)
        print(f"[fetch] {h.id}: 304 Not Modified (ETag) -- reusing {prev['sha256'][:16]}", flush=True)
        return LandedFile(**(prev | {"fixes": tuple(entry.fixes)}))
    filename = _filename(entry.mrf_url, hdr.read_text(encoding="latin-1"))
    landed = _land(h.id, tmp, filename, entry.mrf_url, entry.fixes, data_dir)
    latest.write_text(json.dumps(_portable(landed, data_dir), indent=2), encoding="utf-8")
    return landed


def land_local(hospital_id: str, source: Path, data_dir: Path) -> LandedFile:
    """Land a local file (offline mode / fixtures / deliberately broken inputs) exactly like a download."""
    incoming = data_dir / "raw" / "hpt" / hospital_id / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / "local.part"
    shutil.copyfile(source, tmp)
    return _land(hospital_id, tmp, source.name, None, (), data_dir)

"""Generate the synthetic FHIR R4 population with Synthea (pinned release, pinned seeds, pinned reference date).

Runs the Synthea jar in a Java container (eclipse-temurin:21-jre), so no local Java is needed. With the container pinned
to one CPU, output content is byte-deterministic for a given jar + arguments on any machine (multi-threaded runs are
not -- see the comment in `generate`); only the hospital/practitioner directory *file names* embed a wall-clock
timestamp, which is why staging orders bundles by content hash.
Everything generated is synthetic: no PHI, by construction.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

SYNTHEA_VERSION = "v4.0.0"
JAR_URL = f"https://github.com/synthetichealth/synthea/releases/download/{SYNTHEA_VERSION}/synthea-with-dependencies.jar"
JAR_SHA256 = "ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"
IMAGE = "eclipse-temurin:21-jre"
SEED, REFERENCE_DATE = "20260929", "20260901"
# The simulation END date defaults to *today*, so an unpinned run grows by a day of history every day (found
# 2026-09-30: +6 Observations, +6 Procedures, +1 Claim vs the day before). Pinning it to the reference date makes the
# population independent of the day it is generated, not just of the machine (CP-DEC 018).
END_DATE = REFERENCE_DATE


def generate(root: Path, population: int = 200, log=lambda m: print(m, flush=True)) -> dict:
    """root = data/raw/synthea. Downloads the pinned jar (curl), verifies its hash, runs Synthea, writes a manifest."""
    jar = root / "bin" / f"synthea-with-dependencies-{SYNTHEA_VERSION}.jar"
    if not jar.exists():
        jar.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-sS", "-L", "--fail", "-o", str(jar), JAR_URL], check=True)
    sha = hashlib.sha256(jar.read_bytes()).hexdigest()
    if sha != JAR_SHA256:
        raise RuntimeError(f"Synthea jar hash mismatch: {sha} != pinned {JAR_SHA256}")
    out = root / "output"
    if out.exists():
        shutil.rmtree(out)  # a population is regenerated whole, never appended to
    args = ["-s", SEED, "-cs", SEED, "-r", REFERENCE_DATE, "-e", END_DATE, "-p", str(population),
            "--exporter.baseDirectory=/work/output", "--exporter.fhir.export=true",
            "--exporter.hospital.fhir.export=true", "--exporter.practitioner.fhir.export=true",
            "--exporter.csv.export=false", "--exporter.text.export=false", "Illinois", "Chicago"]
    # On Linux/macOS run as the calling user: the container otherwise writes root-owned output that the pipeline
    # (e.g. the GitHub runner user) cannot then write its manifest into. Docker Desktop on Windows maps ownership itself.
    user = ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"] if hasattr(os, "getuid") else []
    # --cpus=1: Synthea generates people on a thread pool sized to the CPUs, and with more than one thread its output is
    # NOT byte-deterministic (two identical 16-core runs differed in one patient; 16-core vs 4-core runs in 11). With one
    # CPU, runs are byte-identical -- on any machine. ~80 s instead of ~22 s for 200 patients.
    cmd = ["docker", "run", "--rm", "--cpus=1", *user, "-v", f"{root.resolve().as_posix()}:/work", "-w", "/work", IMAGE,
           "java", "-jar", f"bin/{jar.name}", *args]
    log(f"[synthea] {SYNTHEA_VERSION} population={population} seed={SEED} reference={REFERENCE_DATE}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    manifest = {"synthea_version": SYNTHEA_VERSION, "jar_sha256": sha, "image": IMAGE, "args": args,
                "bundles": len(list((out / "fhir").glob("*.json")))}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log(f"[synthea] wrote {manifest['bundles']} bundles")
    return manifest

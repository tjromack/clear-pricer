"""Generate the synthetic FHIR R4 population with Synthea (pinned release, pinned seeds, pinned reference date).

Runs the Synthea jar in a Java container (eclipse-temurin:21-jre), so no local Java is needed. Output content is
deterministic for a given jar + arguments (verified: two runs byte-identical); only the hospital/practitioner
directory *file names* embed a wall-clock timestamp, which is why staging orders bundles by content hash.
Everything generated is synthetic: no PHI, by construction.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

SYNTHEA_VERSION = "v4.0.0"
JAR_URL = f"https://github.com/synthetichealth/synthea/releases/download/{SYNTHEA_VERSION}/synthea-with-dependencies.jar"
JAR_SHA256 = "ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"
IMAGE = "eclipse-temurin:21-jre"
SEED, REFERENCE_DATE = "20260929", "20260901"


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
    args = ["-s", SEED, "-cs", SEED, "-r", REFERENCE_DATE, "-p", str(population),
            "--exporter.baseDirectory=/work/output", "--exporter.fhir.export=true",
            "--exporter.hospital.fhir.export=true", "--exporter.practitioner.fhir.export=true",
            "--exporter.csv.export=false", "--exporter.text.export=false", "Illinois", "Chicago"]
    cmd = ["docker", "run", "--rm", "-v", f"{root.resolve().as_posix()}:/work", "-w", "/work", IMAGE,
           "java", "-jar", f"bin/{jar.name}", *args]
    log(f"[synthea] {SYNTHEA_VERSION} population={population} seed={SEED} reference={REFERENCE_DATE}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    manifest = {"synthea_version": SYNTHEA_VERSION, "jar_sha256": sha, "image": IMAGE, "args": args,
                "bundles": len(list((out / "fhir").glob("*.json")))}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log(f"[synthea] wrote {manifest['bundles']} bundles")
    return manifest

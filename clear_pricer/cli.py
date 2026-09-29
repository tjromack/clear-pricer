"""clear-pricer command line.

    clear-pricer run rush                      # discover -> fetch (curl) -> stage -> dbt build (tests gate the run)
    clear-pricer run rush --source-file F.csv  # same, from a local file (offline / fixtures / broken inputs)
    clear-pricer build                         # dbt build only, over whatever is staged

Exit code is non-zero if any step or any dbt test fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def dbt_build(data_dir: Path) -> bool:
    from dbt.cli.main import dbtRunner

    warehouse = data_dir / "warehouse"
    warehouse.mkdir(parents=True, exist_ok=True)
    os.environ["CLEAR_PRICER_DUCKDB"] = (warehouse / "clear_pricer.duckdb").as_posix()
    os.environ.setdefault("DO_NOT_TRACK", "1")
    project = REPO / "dbt"
    res = dbtRunner().invoke([
        "build", "--project-dir", str(project), "--profiles-dir", str(project),
        "--vars", json.dumps({"data_dir": data_dir.as_posix()}),
    ])
    return bool(res.success)


def cmd_run(args: argparse.Namespace) -> int:
    from clear_pricer.fetch import fetch, land_local
    from clear_pricer.sources import HOSPITALS
    from clear_pricer.stage import stage

    data_dir = Path(args.data_dir).resolve()
    h = HOSPITALS.get(args.hospital)
    if h is None:
        print(f"unknown hospital {args.hospital!r}; known: {', '.join(HOSPITALS)}", file=sys.stderr)
        return 2
    landed = land_local(h.id, Path(args.source_file), data_dir) if args.source_file else fetch(h, data_dir)
    print(f"[land]  {h.id}: {landed.filename}  sha256={landed.sha256[:16]}  {landed.bytes:,} bytes")
    stats = stage(h, landed, data_dir)
    print(f"[stage] {h.id}: " + "  ".join(f"{k}={v:,}" for k, v in stats.items()))
    ok = dbt_build(data_dir)
    print("[gate]  PASS" if ok else "[gate]  FAIL -- see dbt output above", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    return 0 if dbt_build(Path(args.data_dir).resolve()) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clear-pricer")
    p.add_argument("--data-dir", default=str(REPO / "data"))
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="land + stage one hospital, then dbt build (gates)")
    r.add_argument("hospital")
    r.add_argument("--source-file", help="use a local file instead of fetching")
    r.set_defaults(func=cmd_run)
    b = sub.add_parser("build", help="dbt build over the current staging")
    b.set_defaults(func=cmd_build)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

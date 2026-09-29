"""clear-pricer command line.

    clear-pricer run rush                      # stage + build: the local one-command path
    clear-pricer run rush --source-file F.csv  # same, from a local file (offline / fixtures / broken inputs)
    clear-pricer stage rush                    # discover -> fetch (curl) -> land -> parse -> stage   (Airflow task)
    clear-pricer build                         # dbt build over everything staged; tests gate the run (Airflow task)
    clear-pricer publish                       # copy the marts to Postgres + parity check           (Airflow task)
    clear-pricer nppes-sync [--reapply]        # NPPES: latest full + weeklies -> CDC history          (Airflow task)
    clear-pricer report                        # regenerate docs/results/npi-reconciliation.md from the warehouse

Exit code is non-zero if any step, dbt test, or parity check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def warehouse_path(data_dir: Path) -> Path:
    return data_dir / "warehouse" / "clear_pricer.duckdb"


def nppes_state_path(data_dir: Path) -> Path:
    """NPPES history is *state* (CDC), so it lives apart from the rebuildable warehouse."""
    return data_dir / "warehouse" / "nppes_state.duckdb"


def dbt_build(data_dir: Path) -> bool:
    from dbt.cli.main import dbtRunner

    from clear_pricer.nppes import init_state

    warehouse_path(data_dir).parent.mkdir(parents=True, exist_ok=True)
    init_state(nppes_state_path(data_dir))  # an empty history is valid (HPT-only runs, clean clones)
    os.environ["CLEAR_PRICER_DUCKDB"] = warehouse_path(data_dir).as_posix()
    os.environ["CLEAR_PRICER_NPPES_DB"] = nppes_state_path(data_dir).as_posix()
    os.environ.setdefault("DO_NOT_TRACK", "1")
    project = REPO / "dbt"
    res = dbtRunner().invoke([
        "build", "--project-dir", str(project), "--profiles-dir", str(project),
        "--vars", json.dumps({"data_dir": data_dir.as_posix()}),
    ])
    return bool(res.success)


def _stage(hospital: str, source_file: str | None, data_dir: Path) -> int:
    from clear_pricer.fetch import fetch, land_local
    from clear_pricer.sources import HOSPITALS
    from clear_pricer.stage import stage

    h = HOSPITALS.get(hospital)
    if h is None:
        print(f"unknown hospital {hospital!r}; known: {', '.join(HOSPITALS)}", file=sys.stderr)
        return 2
    landed = land_local(h.id, Path(source_file), data_dir) if source_file else fetch(h, data_dir)
    print(f"[land]  {h.id}: {landed.filename}  sha256={landed.sha256[:16]}  {landed.bytes:,} bytes", flush=True)
    stats = stage(h, landed, data_dir)
    print(f"[stage] {h.id}: " + "  ".join(f"{k}={v:,}" for k, v in stats.items()), flush=True)
    return 0


def _gate(ok: bool, what: str) -> int:
    print(f"[{what}]  PASS" if ok else f"[{what}]  FAIL -- see output above", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def cmd_stage(args: argparse.Namespace) -> int:
    return _stage(args.hospital, args.source_file or None, Path(args.data_dir).resolve())


def cmd_build(args: argparse.Namespace) -> int:
    return _gate(dbt_build(Path(args.data_dir).resolve()), "gate")


def cmd_run(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).resolve()
    rc = _stage(args.hospital, args.source_file, data_dir)
    return rc or _gate(dbt_build(data_dir), "gate")


def cmd_publish(args: argparse.Namespace) -> int:
    from clear_pricer.publish import publish

    dsn = args.dsn or os.environ.get("CLEAR_PRICER_PG_DSN")
    if not dsn:
        print("set CLEAR_PRICER_PG_DSN or pass --dsn", file=sys.stderr)
        return 2
    return _gate(publish(warehouse_path(Path(args.data_dir).resolve()), dsn), "parity")


def cmd_nppes_sync(args: argparse.Namespace) -> int:
    from clear_pricer.nppes import sync

    data_dir = Path(args.data_dir).resolve()
    results = sync(data_dir, nppes_state_path(data_dir), reapply=args.reapply)
    return 1 if any(r["outcome"] == "rejected_schema" for r in results) else 0


def cmd_report(args: argparse.Namespace) -> int:
    from clear_pricer.report import write

    target = write(warehouse_path(Path(args.data_dir).resolve()), REPO / "docs" / "results" / "npi-reconciliation.md")
    print(f"[report] wrote {target.relative_to(REPO)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clear-pricer")
    p.add_argument("--data-dir", default=os.environ.get("CLEAR_PRICER_DATA_DIR", str(REPO / "data")))
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, func, helptext in (("run", cmd_run, "stage one hospital, then dbt build (gates)"),
                                 ("stage", cmd_stage, "land + parse + stage one hospital")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("hospital")
        s.add_argument("--source-file", help="use a local file instead of fetching")
        s.set_defaults(func=func)
    sub.add_parser("build", help="dbt build over the current staging").set_defaults(func=cmd_build)
    pub = sub.add_parser("publish", help="copy marts to Postgres, then check parity")
    pub.add_argument("--dsn", help="libpq DSN (default: $CLEAR_PRICER_PG_DSN)")
    pub.set_defaults(func=cmd_publish)
    ns = sub.add_parser("nppes-sync", help="apply the latest NPPES full file + weekly deltas (CDC)")
    ns.add_argument("--reapply", action="store_true", help="re-apply already-applied files (idempotency proof)")
    ns.set_defaults(func=cmd_nppes_sync)
    sub.add_parser("report", help="regenerate the committed reconciliation figures").set_defaults(func=cmd_report)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

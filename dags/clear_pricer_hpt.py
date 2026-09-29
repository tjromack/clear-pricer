"""clear-pricer: hospital price transparency pipeline (v1: three Chicago hospitals).

    stage_<hospital> (x3) + nppes_sync + stage_fhir  (parallel)  ->  dbt_build_gates  ->  publish_postgres

- stage_*: discover via cms-hpt.txt -> curl -> content-addressed landing -> parse -> staging Parquet.
  An unchanged upstream file is a no-op at landing and yields byte-identical staging (design pin 2).
- nppes_sync: NPPES latest full + weekly deltas -> CDC type-2 history (a no-op when nothing new was published).
- stage_fhir: parse + validate the synthetic Synthea FHIR R4 bundles (generated once, deterministically, by
  `clear-pricer synthea-generate`; no PHI by construction, and a gate proves it).
- dbt_build_gates: builds the DuckDB warehouse and runs every dbt test. Any failing test fails this task and
  therefore the run (design pin 4); publish never runs on a red build, so Postgres keeps the last gated build.
- publish_postgres: copies the marts to the served Postgres and checks parity against DuckDB (design pin 5).

Prove the gate: trigger with conf {"source_overrides": {"rush": "tests/fixtures/broken_ragged_rows.csv"}}.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param

HOSPITALS = ("rush", "uchicago", "nm")
CLI = "$CLEAR_PRICER_PY -m clear_pricer.cli"

with DAG(
    dag_id="clear_pricer_hpt",
    description="CMS hospital price files -> gated DuckDB warehouse -> served Postgres",
    schedule="@daily",
    start_date=datetime(2026, 9, 1),
    catchup=False,
    max_active_runs=1,  # one writer to the warehouse at a time
    params={
        "source_overrides": Param({}, type="object",
                                  description="hospital -> repo-relative local file, instead of fetching"),
    },
    default_args={"cwd": "/opt/clear-pricer", "retries": 0},
    tags=["clear-pricer", "hpt"],
) as dag:
    stages = [
        BashOperator(
            task_id=f"stage_{h}",
            bash_command=f"{CLI} stage {h} --source-file \"{{{{ params.source_overrides.get('{h}', '') }}}}\"",
            retries=2,  # network fetches only; parse/validation problems are not retried into silence
            retry_delay=timedelta(minutes=5),
            execution_timeout=timedelta(hours=1),
        )
        for h in HOSPITALS
    ]
    nppes_sync = BashOperator(
        task_id="nppes_sync",
        bash_command=f"{CLI} nppes-sync",
        retries=2,
        retry_delay=timedelta(minutes=5),
        execution_timeout=timedelta(hours=2),
    )
    stage_fhir = BashOperator(task_id="stage_fhir", bash_command=f"{CLI} fhir-stage",
                              execution_timeout=timedelta(hours=1))
    gates = BashOperator(task_id="dbt_build_gates", bash_command=f"{CLI} build",
                         execution_timeout=timedelta(hours=1))
    publish = BashOperator(task_id="publish_postgres", bash_command=f"{CLI} publish",
                           execution_timeout=timedelta(hours=1))
    [*stages, nppes_sync, stage_fhir] >> gates >> publish

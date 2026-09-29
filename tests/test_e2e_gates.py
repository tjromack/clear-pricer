"""The gate must fail the run when it should (design pin 4). Full land -> stage -> dbt build, per fixture."""

from pathlib import Path

import pytest

from clear_pricer.cli import main

FIX = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.e2e


def _run(tmp_path: Path, fixture: str) -> int:
    return main(["--data-dir", str(tmp_path / "data"), "run", "rush", "--source-file", str(FIX / fixture)])


def test_clean_sample_passes(tmp_path):
    assert _run(tmp_path, "rush_sample.csv") == 0


@pytest.mark.parametrize("fixture", [
    "broken_missing_required_column.csv",   # -> assert_required_columns_present
    "broken_ragged_rows.csv",               # -> assert_quarantine_within_tolerance
    "broken_template_version.csv",          # -> accepted_values(template_version)
])
def test_broken_input_fails_the_run(tmp_path, fixture):
    assert _run(tmp_path, fixture) == 1

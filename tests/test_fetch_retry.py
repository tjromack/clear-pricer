"""Hosted-run resilience: a download the network breaks is retried; anything else fails at once.

Found 2026-10-07: Northwestern's server cancelled the 5 GB download 23 minutes in (curl exit 92, HTTP/2 stream reset)
and the hosted run failed, where the Airflow DAG would have retried the task.
"""

import subprocess

import pytest

from clear_pricer import fetch


def _fake_run(exit_codes: list[int], calls: list):
    def run(cmd, capture_output, text):
        code = exit_codes[len(calls)]
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, code, stdout="200" if code == 0 else "", stderr=f"curl: ({code})")
    return run


def _call(monkeypatch, tmp_path, exit_codes):
    calls: list = []
    monkeypatch.setattr(fetch.subprocess, "run", _fake_run(exit_codes, calls))
    try:
        return fetch._curl("https://example.org/f.json", tmp_path / "f", wait_s=0, log=lambda m: None), calls
    except fetch.FetchError as e:
        return e, calls


def test_a_stream_reset_is_retried(monkeypatch, tmp_path):
    status, calls = _call(monkeypatch, tmp_path, [92, 0])
    assert status == 200 and len(calls) == 2


def test_retries_are_bounded(monkeypatch, tmp_path):
    err, calls = _call(monkeypatch, tmp_path, [92, 56])
    assert isinstance(err, fetch.FetchError) and "after 2 attempt(s)" in str(err) and len(calls) == fetch.ATTEMPTS


@pytest.mark.parametrize("code", [22, 23, 60])  # HTTP error (--fail), write error, TLS certificate problem
def test_non_network_failures_are_not_retried(monkeypatch, tmp_path, code):
    err, calls = _call(monkeypatch, tmp_path, [code, 0])
    assert isinstance(err, fetch.FetchError) and len(calls) == 1

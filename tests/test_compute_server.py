"""
Compute server tests — run_script and run_bash execution, output capping, timeouts.
"""

import subprocess
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def workspace_dir(tmp_path, monkeypatch):
    """Point the compute server at a temp directory."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    import importlib
    import servers.compute.server as cs
    importlib.reload(cs)
    yield tmp_path


def _server():
    import servers.compute.server as cs
    return cs


# ── run_script ─────────────────────────────────────────────────────────────


def test_run_script_returns_stdout():
    cs = _server()
    result = cs.run_script("print('hello world')")
    assert "hello world" in result


def test_run_script_captures_stderr_on_exception():
    cs = _server()
    result = cs.run_script("raise ValueError('boom')")
    assert "stderr:" in result
    assert "ValueError" in result
    assert "boom" in result


def test_run_script_output_capped():
    cs = _server()
    # Generate output larger than _MAX_OUTPUT (1MB)
    result = cs.run_script("print('x' * 2_000_000)")
    assert "truncated" in result


# ── run_bash ───────────────────────────────────────────────────────────────


def test_run_bash_returns_stdout():
    cs = _server()
    result = cs.run_bash("echo 'hello bash'")
    assert "hello bash" in result


def test_run_bash_returns_stderr_and_exit_code_on_failure():
    cs = _server()
    result = cs.run_bash("echo 'oops' >&2; exit 42")
    assert "oops" in result
    assert "exit code: 42" in result


def test_run_bash_timeout(monkeypatch):
    cs = _server()
    with patch("servers.compute.server.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="bash", timeout=300)):
        result = cs.run_bash("sleep 999")
    assert "timed out" in result


def test_run_bash_output_capped():
    cs = _server()
    # Use python to generate > 1MB of output via bash
    result = cs.run_bash("python3 -c \"print('x' * 2_000_000)\"")
    assert "truncated" in result

"""
Log Analyzer server tests — parsing, error summary, anomalies, timeline, path traversal.
"""

import importlib
import importlib.util
import json
import os

import pytest

SYSLOG_CONTENT = """\
2026-04-10T10:00:00Z INFO [api] Request processed
2026-04-10T10:00:01Z ERROR [db] Connection timeout after 30s
2026-04-10T10:00:02Z ERROR [db] Connection timeout after 45s
2026-04-10T10:00:03Z INFO [api] Request processed
2026-04-10T10:01:00Z ERROR [auth] Invalid token for user 12345
"""

JSONL_CONTENT = """\
{"timestamp": "2026-04-10T10:00:00Z", "level": "INFO", "module": "api", "message": "Request OK"}
{"timestamp": "2026-04-10T10:00:01Z", "level": "ERROR", "module": "db", "message": "Timeout 500ms"}
{"timestamp": "2026-04-10T10:00:02Z", "level": "ERROR", "module": "db", "message": "Timeout 600ms"}
{"timestamp": "2026-04-10T10:00:03Z", "level": "WARN", "module": "cache", "message": "Eviction spike"}
"""


@pytest.fixture(autouse=True)
def log_mod(tmp_path, monkeypatch):
    """Import log-analyzer server with COOPERAGE_WORKSPACE pointing at tmp_path."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "log_analyzer_server",
        os.path.join(os.path.dirname(__file__), "..", "example-servers", "log-analyzer", "server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ws(tmp_path):
    return tmp_path


@pytest.fixture
def syslog_file(ws):
    p = ws / "app.log"
    p.write_text(SYSLOG_CONTENT)
    return p


@pytest.fixture
def jsonl_file(ws):
    p = ws / "app.jsonl"
    p.write_text(JSONL_CONTENT)
    return p


# ── parse_logs (JSON-lines) ─────────────────────────────────────────────────


def test_parse_logs_jsonl_event_count(log_mod, ws, jsonl_file):
    result = json.loads(log_mod.parse_logs("app.jsonl", output="parsed.json"))
    assert result["parsed_events"] == 4
    assert result["by_level"]["ERROR"] == 2
    assert result["by_level"]["INFO"] == 1
    assert result["by_level"]["WARN"] == 1


def test_parse_logs_jsonl_saves_json(log_mod, ws, jsonl_file):
    log_mod.parse_logs("app.jsonl", output="parsed.json")
    parsed = json.loads((ws / "parsed.json").read_text())
    assert len(parsed) == 4
    assert parsed[0]["message"] == "Request OK"


# ── parse_logs (syslog) ─────────────────────────────────────────────────────


def test_parse_logs_syslog(log_mod, ws, syslog_file):
    result = json.loads(log_mod.parse_logs("app.log", output="parsed_sys.json"))
    assert result["parsed_events"] == 5
    assert result["by_level"]["INFO"] == 2
    assert result["by_level"]["ERROR"] == 3


# ── error_summary ────────────────────────────────────────────────────────────


def test_error_summary_groups_by_pattern(log_mod, ws, syslog_file):
    log_mod.parse_logs("app.log", output="parsed.json")
    result = json.loads(log_mod.error_summary("parsed.json"))
    assert result["total_errors"] == 3
    patterns = result["top_patterns"]
    assert len(patterns) >= 1
    # Timeout errors appear in patterns; token error appears separately
    timeout_patterns = [p for p in patterns if "timeout" in p["pattern"].lower()]
    assert len(timeout_patterns) >= 1
    token_patterns = [p for p in patterns if "token" in p["pattern"].lower()]
    assert len(token_patterns) == 1


def test_error_summary_normalizes_numbers(log_mod, ws, syslog_file):
    log_mod.parse_logs("app.log", output="parsed.json")
    result = json.loads(log_mod.error_summary("parsed.json"))
    patterns = result["top_patterns"]
    token_pattern = [p for p in patterns if "token" in p["pattern"].lower()]
    assert len(token_pattern) == 1
    # "user 12345" should become "user <N>"
    assert "<N>" in token_pattern[0]["pattern"]


# ── detect_anomalies ────────────────────────────────────────────────────────


def test_detect_anomalies_finds_spike(log_mod, ws):
    """Create logs with a clear spike in one window."""
    lines = []
    # Normal windows: 1 error each in minutes 00-04, 10-14, 15-19 (spread across buckets)
    for minute in [0, 1, 2, 3, 4, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]:
        lines.append(f"2026-04-10T10:{minute:02d}:00Z INFO [api] OK")
        lines.append(f"2026-04-10T10:{minute:02d}:30Z ERROR [db] fail")
    # Spike window: 50 errors in minute 05
    for sec in range(50):
        lines.append(f"2026-04-10T10:05:{sec:02d}Z ERROR [db] fail")

    logfile = ws / "spike.log"
    logfile.write_text("\n".join(lines))
    log_mod.parse_logs("spike.log", output="spike_parsed.json")
    result = json.loads(log_mod.detect_anomalies("spike_parsed.json", window_minutes=5, threshold_std=1.0))
    assert len(result["anomalies"]) >= 1
    # The spike window should have the highest error count
    spike = max(result["anomalies"], key=lambda a: a["error_count"])
    assert spike["error_count"] >= 20


# ── incident_timeline ───────────────────────────────────────────────────────


def test_incident_timeline_filters_by_window(log_mod, ws, syslog_file):
    log_mod.parse_logs("app.log", output="parsed.json")
    result = json.loads(log_mod.incident_timeline(
        "parsed.json",
        start_time="2026-04-10T10:00:00Z",
        end_time="2026-04-10T10:00:03Z",
        levels=["ERROR"],
    ))
    assert result["total_matched"] == 2
    for event in result["events"]:
        assert event["level"] == "ERROR"


def test_incident_timeline_multiple_levels(log_mod, ws, syslog_file):
    log_mod.parse_logs("app.log", output="parsed.json")
    result = json.loads(log_mod.incident_timeline(
        "parsed.json",
        start_time="2026-04-10T10:00:00Z",
        end_time="2026-04-10T10:02:00Z",
        levels=["ERROR", "INFO"],
    ))
    # Should include both INFO and ERROR events in the window
    assert result["total_matched"] >= 4


# ── _safe_path ───────────────────────────────────────────────────────────────


def test_safe_path_blocks_traversal(log_mod):
    with pytest.raises(ValueError, match="escapes workspace"):
        log_mod._safe_path("../../etc/passwd")

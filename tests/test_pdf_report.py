"""
PDF Report server tests — report generation, CSV inclusion, missing files, path traversal.
"""

import importlib
import importlib.util
import json
import os

import pytest

reportlab = pytest.importorskip("reportlab")


@pytest.fixture(autouse=True)
def pdf_mod(tmp_path, monkeypatch):
    """Import pdf-report server with COOPERAGE_WORKSPACE pointing at tmp_path."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "pdf_report_server",
        os.path.join(os.path.dirname(__file__), "..", "example-servers", "pdf-report", "server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ws(tmp_path):
    return tmp_path


# ── generate_report (text sections) ────────────────────────────────────────


def test_generate_report_creates_pdf(pdf_mod, ws):
    sections = [
        {"heading": "Introduction", "text": "This is a test report."},
        {"heading": "Details", "text": "More details here."},
    ]
    result = json.loads(pdf_mod.generate_report("Test Report", sections, output="test.pdf"))
    assert result["report"] == "test.pdf"
    assert result["sections"] == 2
    assert (ws / "test.pdf").exists()
    assert (ws / "test.pdf").stat().st_size > 0


# ── generate_report (with CSV) ─────────────────────────────────────────────


def test_generate_report_with_csv(pdf_mod, ws):
    csv_file = ws / "data.csv"
    csv_file.write_text("name,value\nAlpha,100\nBeta,200\n")
    sections = [
        {"heading": "Data Table", "file": "data.csv"},
    ]
    result = json.loads(pdf_mod.generate_report("CSV Report", sections, output="csv_report.pdf"))
    assert result["report"] == "csv_report.pdf"
    assert (ws / "csv_report.pdf").exists()
    assert (ws / "csv_report.pdf").stat().st_size > 0


# ── generate_report (missing file) ─────────────────────────────────────────


def test_generate_report_missing_file_no_crash(pdf_mod, ws):
    sections = [
        {"heading": "Missing", "file": "nonexistent.csv"},
    ]
    result = json.loads(pdf_mod.generate_report("Missing File Report", sections, output="missing.pdf"))
    assert result["report"] == "missing.pdf"
    assert (ws / "missing.pdf").exists()


# ── _safe_path ──────────────────────────────────────────────────────────────


def test_safe_path_blocks_traversal(pdf_mod):
    with pytest.raises(ValueError, match="escapes workspace"):
        pdf_mod._safe_path("../../etc/passwd")

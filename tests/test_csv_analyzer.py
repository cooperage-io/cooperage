"""
CSV Analyzer server tests — stats, plotting, comparison, path traversal.
"""

import importlib
import importlib.util
import json
import os

import pytest


@pytest.fixture(autouse=True)
def csv_mod(tmp_path, monkeypatch):
    """Import csv-analyzer server with COOPERAGE_WORKSPACE pointing at tmp_path."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "csv_analyzer_server",
        os.path.join(os.path.dirname(__file__), "..", "example-servers", "csv-analyzer", "server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ws(tmp_path):
    return tmp_path


def _mod(csv_mod):
    return csv_mod


# ── analyze_csv ─────────────────────────────────────────────────────────────


def test_analyze_csv_row_column_counts(csv_mod, ws):
    csv_file = ws / "data.csv"
    csv_file.write_text("name,age,score\nAlice,30,85\nBob,25,90\nCarol,35,78\n")
    result = json.loads(csv_mod.analyze_csv("data.csv"))
    assert result["shape"]["rows"] == 3
    assert result["shape"]["columns"] == 3


def test_analyze_csv_column_types_and_missing(csv_mod, ws):
    csv_file = ws / "missing.csv"
    csv_file.write_text("x,y\n1,a\n2,\n3,c\n")
    result = json.loads(csv_mod.analyze_csv("missing.csv"))
    assert "x" in result["columns"]
    assert "y" in result["columns"]
    assert result["missing"]["y"] == 1
    assert result["missing"]["x"] == 0


# ── plot_column ─────────────────────────────────────────────────────────────


def test_plot_column_creates_png(csv_mod, ws):
    csv_file = ws / "plot.csv"
    csv_file.write_text("category,value\nA,10\nB,20\nC,30\n")
    result = json.loads(csv_mod.plot_column("plot.csv", "category", chart_type="bar", output="out.png"))
    assert result["chart"] == "out.png"
    assert (ws / "out.png").exists()
    assert (ws / "out.png").stat().st_size > 0


def test_plot_column_nonexistent_column(csv_mod, ws):
    csv_file = ws / "plot2.csv"
    csv_file.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="not found"):
        csv_mod.plot_column("plot2.csv", "zzz")


# ── compare_csvs ────────────────────────────────────────────────────────────


def test_compare_csvs_shared_and_only(csv_mod, ws):
    (ws / "a.csv").write_text("x,y,z\n1,2,3\n4,5,6\n")
    (ws / "b.csv").write_text("x,y,w\n10,20,30\n")
    result = json.loads(csv_mod.compare_csvs("a.csv", "b.csv"))
    assert sorted(result["shared_columns"]) == ["x", "y"]
    assert result["only_in_a"] == ["z"]
    assert result["only_in_b"] == ["w"]


# ── _safe_path ──────────────────────────────────────────────────────────────


def test_safe_path_blocks_traversal(csv_mod):
    with pytest.raises(ValueError, match="escapes workspace"):
        csv_mod._safe_path("../../etc/passwd")

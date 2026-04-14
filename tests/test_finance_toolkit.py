"""
Finance Toolkit server tests — prices, portfolio, correlation, risk, path traversal.
"""

import importlib
import importlib.util
import json
import os

import pytest


@pytest.fixture(autouse=True)
def fin_mod(tmp_path, monkeypatch):
    """Import finance-toolkit server with COOPERAGE_WORKSPACE pointing at tmp_path."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "finance_toolkit_server",
        os.path.join(os.path.dirname(__file__), "..", "example-servers", "finance-toolkit", "server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ws(tmp_path):
    return tmp_path


@pytest.fixture
def prices_file(fin_mod):
    """Create price data for AAPL and MSFT."""
    result = json.loads(fin_mod.fetch_prices(["AAPL", "MSFT"], days=60, output="prices.csv"))
    return result


# ── fetch_prices ─────────────────────────────────────────────────────────────


def test_fetch_prices_saves_csv(fin_mod, ws):
    result = json.loads(fin_mod.fetch_prices(["AAPL", "GOOG"], days=30, output="test_prices.csv"))
    assert (ws / "test_prices.csv").exists()
    assert "AAPL" in result["tickers"]
    assert "GOOG" in result["tickers"]
    assert result["rows"] == 60  # 30 days x 2 tickers


def test_fetch_prices_csv_columns(fin_mod, ws):
    fin_mod.fetch_prices(["TSLA"], days=10, output="cols.csv")
    import pandas as pd
    df = pd.read_csv(ws / "cols.csv")
    for col in ["date", "ticker", "open", "high", "low", "close", "volume"]:
        assert col in df.columns


# ── analyze_portfolio ────────────────────────────────────────────────────────


def test_analyze_portfolio_returns_stats(fin_mod, ws, prices_file):
    result = json.loads(fin_mod.analyze_portfolio("prices.csv"))
    assert "AAPL" in result["tickers"]
    assert "MSFT" in result["tickers"]
    for ticker_stats in result["tickers"].values():
        assert "annualized_return" in ticker_stats
        assert "annualized_volatility" in ticker_stats
        assert "sharpe_ratio" in ticker_stats
    assert "portfolio" in result
    assert "annualized_return" in result["portfolio"]


# ── correlation_matrix ───────────────────────────────────────────────────────


def test_correlation_matrix_creates_png(fin_mod, ws, prices_file):
    result = json.loads(fin_mod.correlation_matrix("prices.csv", output="corr.png"))
    assert (ws / "corr.png").exists()
    assert "matrix" in result
    assert "AAPL" in result["matrix"]
    assert "MSFT" in result["matrix"]


# ── risk_report ──────────────────────────────────────────────────────────────


def test_risk_report_returns_var_and_drawdown(fin_mod, ws, prices_file):
    result = json.loads(fin_mod.risk_report("prices.csv", confidence=0.95))
    assert result["confidence_level"] == 0.95
    assert "risk_metrics" in result
    for ticker, metrics in result["risk_metrics"].items():
        assert "var_95" in metrics
        assert "cvar_95" in metrics
        assert "max_drawdown_pct" in metrics
        assert "worst_day_pct" in metrics
        assert "best_day_pct" in metrics


# ── _safe_path ───────────────────────────────────────────────────────────────


def test_safe_path_blocks_traversal(fin_mod):
    with pytest.raises(ValueError, match="escapes workspace"):
        fin_mod._safe_path("../../etc/passwd")

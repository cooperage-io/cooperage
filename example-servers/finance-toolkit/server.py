"""
Cooperage Example: Finance Toolkit

Fetches market data, analyzes portfolios, computes risk metrics, and
generates performance charts. Demonstrates API integration + compute
in a financial context.

Tools:
  fetch_prices       — fetch historical stock prices (mock data for demo)
  analyze_portfolio  — compute returns, volatility, Sharpe ratio
  correlation_matrix — compute and chart correlation between assets
  risk_report        — generate a risk summary with VaR and drawdown
"""

import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import uvicorn
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("finance-toolkit", json_response=True, stateless_http=True)


def _safe_path(filename: str) -> Path:
    resolved = (WORKSPACE / filename).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path {filename!r} escapes workspace")
    return resolved


def _generate_mock_prices(ticker: str, days: int, seed: int | None = None) -> pd.DataFrame:
    """Generate realistic-looking mock stock price data."""
    rng = np.random.RandomState(seed or hash(ticker) % 2**31)
    base_price = 50 + rng.random() * 200  # $50-$250

    dates = pd.date_range(end=datetime.now(timezone.utc).date(), periods=days, freq="B")
    returns = rng.normal(0.0005, 0.02, size=days)  # slight upward drift, 2% daily vol
    prices = base_price * np.cumprod(1 + returns)

    # Add volume
    volume = (rng.lognormal(15, 1, size=days)).astype(int)

    df = pd.DataFrame({
        "date": dates,
        "ticker": ticker,
        "open": prices * (1 + rng.uniform(-0.01, 0.01, days)),
        "high": prices * (1 + rng.uniform(0, 0.03, days)),
        "low": prices * (1 - rng.uniform(0, 0.03, days)),
        "close": prices,
        "volume": volume,
    })
    return df.round(2)


@mcp.tool()
def fetch_prices(
    tickers: list[str],
    days: int = 252,
    output: str = "prices.csv",
) -> str:
    """Fetch historical price data for a list of stock tickers.
    Saves combined CSV to /workspace. Uses mock data for demo purposes."""
    frames = []
    for ticker in tickers:
        df = _generate_mock_prices(ticker.upper(), days)
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    out_path = _safe_path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path, index=False)

    return json.dumps({
        "saved_to": output,
        "tickers": [t.upper() for t in tickers],
        "rows": len(combined),
        "date_range": {
            "start": str(combined["date"].min()),
            "end": str(combined["date"].max()),
        },
    }, indent=2)


@mcp.tool()
def analyze_portfolio(
    prices_path: str,
    weights: dict[str, float] | None = None,
) -> str:
    """Compute portfolio-level returns, volatility, and Sharpe ratio from price data.
    If weights not provided, assumes equal-weight across all tickers."""
    df = pd.read_csv(_safe_path(prices_path), parse_dates=["date"])

    tickers = df["ticker"].unique().tolist()
    if weights is None:
        w = 1.0 / len(tickers)
        weights = {t: w for t in tickers}

    # Pivot to get close prices per ticker
    pivot = df.pivot_table(index="date", columns="ticker", values="close")
    daily_returns = pivot.pct_change().dropna()

    results = {}
    for ticker in tickers:
        if ticker not in daily_returns.columns:
            continue
        r = daily_returns[ticker]
        results[ticker] = {
            "weight": round(weights.get(ticker, 0), 4),
            "annualized_return": round(float(r.mean() * 252 * 100), 2),
            "annualized_volatility": round(float(r.std() * np.sqrt(252) * 100), 2),
            "sharpe_ratio": round(float((r.mean() / r.std()) * np.sqrt(252)), 2) if r.std() > 0 else 0,
            "max_drawdown": round(float(((pivot[ticker] / pivot[ticker].cummax()) - 1).min() * 100), 2),
            "total_return": round(float((pivot[ticker].iloc[-1] / pivot[ticker].iloc[0] - 1) * 100), 2),
        }

    # Portfolio-level
    portfolio_returns = sum(
        daily_returns[t] * weights.get(t, 0) for t in tickers if t in daily_returns.columns
    )
    portfolio_cum = (1 + portfolio_returns).cumprod()

    portfolio_stats = {
        "annualized_return": round(float(portfolio_returns.mean() * 252 * 100), 2),
        "annualized_volatility": round(float(portfolio_returns.std() * np.sqrt(252) * 100), 2),
        "sharpe_ratio": round(float((portfolio_returns.mean() / portfolio_returns.std()) * np.sqrt(252)), 2) if portfolio_returns.std() > 0 else 0,
        "total_return": round(float((portfolio_cum.iloc[-1] - 1) * 100), 2),
    }

    return json.dumps({
        "tickers": results,
        "portfolio": portfolio_stats,
    }, indent=2)


@mcp.tool()
def correlation_matrix(prices_path: str, output: str = "correlation.png") -> str:
    """Compute and chart the correlation matrix between asset returns."""
    df = pd.read_csv(_safe_path(prices_path), parse_dates=["date"])
    pivot = df.pivot_table(index="date", columns="ticker", values="close")
    corr = pivot.pct_change().dropna().corr()

    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(corr.values, cmap="RdYlGn", vmin=-1, vmax=1)
    ax.set_xticks(range(len(corr.columns)))
    ax.set_yticks(range(len(corr.columns)))
    ax.set_xticklabels(corr.columns, rotation=45, ha="right")
    ax.set_yticklabels(corr.columns)

    # Annotate cells
    for i in range(len(corr)):
        for j in range(len(corr)):
            ax.text(j, i, f"{corr.values[i, j]:.2f}", ha="center", va="center", fontsize=10)

    plt.colorbar(im, ax=ax, label="Correlation")
    ax.set_title("Return Correlation Matrix")
    plt.tight_layout()

    out_path = _safe_path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return json.dumps({
        "chart": output,
        "matrix": json.loads(corr.to_json()),
    }, indent=2)


@mcp.tool()
def risk_report(prices_path: str, confidence: float = 0.95) -> str:
    """Compute risk metrics: Value-at-Risk (VaR), Expected Shortfall (CVaR),
    and maximum drawdown for each ticker and the equal-weight portfolio."""
    df = pd.read_csv(_safe_path(prices_path), parse_dates=["date"])
    pivot = df.pivot_table(index="date", columns="ticker", values="close")
    daily_returns = pivot.pct_change().dropna()

    tickers = daily_returns.columns.tolist()
    results = {}

    for ticker in tickers:
        r = daily_returns[ticker].values
        var = float(np.percentile(r, (1 - confidence) * 100))
        cvar = float(r[r <= var].mean()) if len(r[r <= var]) > 0 else var
        cum = (1 + daily_returns[ticker]).cumprod()
        max_dd = float(((cum / cum.cummax()) - 1).min())

        results[ticker] = {
            f"var_{int(confidence*100)}": round(var * 100, 2),
            f"cvar_{int(confidence*100)}": round(cvar * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "worst_day_pct": round(float(r.min()) * 100, 2),
            "best_day_pct": round(float(r.max()) * 100, 2),
        }

    return json.dumps({
        "confidence_level": confidence,
        "period_days": len(daily_returns),
        "risk_metrics": results,
    }, indent=2)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)

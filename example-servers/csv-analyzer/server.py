"""
Cooperage Example: CSV Analyzer

Reads CSV files from /workspace, computes statistics, and generates
matplotlib charts. Pairs well with web-scraper (to fetch data) and
pdf-report (to compile results).

Tools:
  analyze_csv    — compute summary statistics on a CSV file
  plot_column    — generate a bar/line/histogram chart from a CSV column
  compare_csvs   — compare two CSV files side by side
"""

import io
import json
import os
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

mcp = FastMCP("csv-analyzer", json_response=True, stateless_http=True)


def _safe_path(filename: str) -> Path:
    resolved = (WORKSPACE / filename).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path {filename!r} escapes workspace")
    return resolved


@mcp.tool()
def analyze_csv(path: str, head: int = 5) -> str:
    """Compute summary statistics on a CSV file in /workspace.
    Returns shape, column types, descriptive stats, and first N rows."""
    df = pd.read_csv(_safe_path(path))
    result = {
        "file": path,
        "shape": {"rows": len(df), "columns": len(df.columns)},
        "columns": {col: str(df[col].dtype) for col in df.columns},
        "stats": json.loads(df.describe(include="all").to_json()),
        "head": json.loads(df.head(head).to_json(orient="records")),
        "missing": {col: int(df[col].isna().sum()) for col in df.columns},
    }
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def plot_column(
    path: str,
    column: str,
    chart_type: str = "bar",
    output: str = "chart.png",
    title: str = "",
) -> str:
    """Generate a chart from a CSV column. chart_type: bar, line, hist, pie.
    Saves to /workspace/{output}."""
    df = pd.read_csv(_safe_path(path))
    if column not in df.columns:
        raise ValueError(f"Column {column!r} not found. Available: {list(df.columns)}")

    fig, ax = plt.subplots(figsize=(10, 6))
    if chart_type == "bar":
        counts = df[column].value_counts().head(20)
        counts.plot(kind="bar", ax=ax)
    elif chart_type == "line":
        df[column].plot(kind="line", ax=ax)
    elif chart_type == "hist":
        df[column].dropna().plot(kind="hist", bins=30, ax=ax)
    elif chart_type == "pie":
        counts = df[column].value_counts().head(10)
        counts.plot(kind="pie", ax=ax, autopct="%1.1f%%")
    else:
        raise ValueError(f"Unknown chart_type: {chart_type!r}. Use bar, line, hist, or pie.")

    ax.set_title(title or f"{column} — {chart_type}")
    plt.tight_layout()

    out_path = _safe_path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return json.dumps({"chart": output, "column": column, "type": chart_type})


@mcp.tool()
def compare_csvs(path_a: str, path_b: str) -> str:
    """Compare two CSV files: row/column counts, shared columns, and
    statistical differences on numeric columns."""
    df_a = pd.read_csv(_safe_path(path_a))
    df_b = pd.read_csv(_safe_path(path_b))

    shared = list(set(df_a.columns) & set(df_b.columns))
    only_a = list(set(df_a.columns) - set(df_b.columns))
    only_b = list(set(df_b.columns) - set(df_a.columns))

    diffs = {}
    for col in shared:
        if pd.api.types.is_numeric_dtype(df_a[col]) and pd.api.types.is_numeric_dtype(df_b[col]):
            diffs[col] = {
                "mean_a": float(df_a[col].mean()),
                "mean_b": float(df_b[col].mean()),
                "mean_diff": float(df_a[col].mean() - df_b[col].mean()),
            }

    result = {
        "file_a": {"path": path_a, "rows": len(df_a), "columns": len(df_a.columns)},
        "file_b": {"path": path_b, "rows": len(df_b), "columns": len(df_b.columns)},
        "shared_columns": sorted(shared),
        "only_in_a": sorted(only_a),
        "only_in_b": sorted(only_b),
        "numeric_diffs": diffs,
    }
    return json.dumps(result, indent=2, default=str)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)

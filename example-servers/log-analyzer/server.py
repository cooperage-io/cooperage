"""
Cooperage Example: Log Analyzer (DevOps/SRE)

Parses application logs from /workspace, detects anomalies, extracts
error patterns, and generates incident timelines. Works with common
log formats: JSON-lines, syslog, Apache/nginx access logs.

Tools:
  parse_logs       — parse a log file, extract structured events
  error_summary    — group and count errors by type/message
  detect_anomalies — find spikes in error rate or latency
  incident_timeline — generate a timeline of events around a time window
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import uvicorn
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("log-analyzer", json_response=True, stateless_http=True)


def _safe_path(filename: str) -> Path:
    resolved = (WORKSPACE / filename).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path {filename!r} escapes workspace")
    return resolved


def _parse_line(line: str) -> dict | None:
    """Try to parse a log line as JSON-lines, syslog, or Apache format."""
    line = line.strip()
    if not line:
        return None

    # JSON-lines
    if line.startswith("{"):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            pass

    # Syslog-ish: "2026-04-10T12:00:00Z ERROR [module] message"
    m = re.match(
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*)\s+(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\s+(?:\[(\w+)\]\s+)?(.*)",
        line,
    )
    if m:
        return {
            "timestamp": m.group(1),
            "level": m.group(2).upper(),
            "module": m.group(3) or "",
            "message": m.group(4),
        }

    # Apache/nginx combined: '127.0.0.1 - - [10/Apr/2026:12:00:00 +0000] "GET /api HTTP/1.1" 500 1234'
    m = re.match(
        r'(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+) \S+" (\d+) (\d+)',
        line,
    )
    if m:
        status = int(m.group(5))
        return {
            "timestamp": m.group(2),
            "ip": m.group(1),
            "method": m.group(3),
            "path": m.group(4),
            "status": status,
            "bytes": int(m.group(6)),
            "level": "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO",
            "message": f"{m.group(3)} {m.group(4)} → {status}",
        }

    # Fallback: treat as unstructured
    return {"message": line, "level": "INFO"}


@mcp.tool()
def parse_logs(path: str, output: str = "parsed_logs.json", max_lines: int = 50000) -> str:
    """Parse a log file from /workspace. Supports JSON-lines, syslog, and
    Apache/nginx formats. Saves structured events to a JSON file."""
    lines = _safe_path(path).read_text().splitlines()[:max_lines]

    events = []
    level_counts = Counter()
    for line in lines:
        event = _parse_line(line)
        if event:
            events.append(event)
            level_counts[event.get("level", "UNKNOWN")] += 1

    out_path = _safe_path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(events, indent=2))

    return json.dumps({
        "file": path,
        "saved_to": output,
        "total_lines": len(lines),
        "parsed_events": len(events),
        "by_level": dict(level_counts.most_common()),
    }, indent=2)


@mcp.tool()
def error_summary(path: str, min_count: int = 1) -> str:
    """Group and count errors from a parsed log file.
    Groups by error message (first 100 chars) and returns top patterns."""
    events = json.loads(_safe_path(path).read_text())
    errors = [e for e in events if e.get("level") in ("ERROR", "FATAL", "CRITICAL")]

    # Group by message pattern (normalize numbers, UUIDs)
    patterns = Counter()
    examples = {}
    for e in errors:
        msg = e.get("message", "")
        # Normalize dynamic parts
        pattern = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*", "<TIMESTAMP>", msg)
        pattern = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<UUID>", pattern)
        pattern = re.sub(r"\b\d+\b", "<N>", pattern)
        pattern = pattern[:100]

        patterns[pattern] += 1
        if pattern not in examples:
            examples[pattern] = msg[:200]

    results = []
    for pattern, count in patterns.most_common():
        if count >= min_count:
            results.append({
                "pattern": pattern,
                "count": count,
                "example": examples[pattern],
            })

    return json.dumps({
        "total_errors": len(errors),
        "unique_patterns": len(patterns),
        "top_patterns": results[:30],
    }, indent=2)


@mcp.tool()
def detect_anomalies(
    path: str,
    window_minutes: int = 5,
    threshold_std: float = 2.0,
) -> str:
    """Detect spikes in error rate by bucketing events into time windows
    and flagging windows with error counts > threshold_std standard deviations
    above the mean."""
    events = json.loads(_safe_path(path).read_text())

    # Bucket errors by time window
    error_buckets = defaultdict(int)
    total_buckets = defaultdict(int)

    for e in events:
        ts = e.get("timestamp", "")
        if not ts:
            continue
        # Truncate to window
        try:
            # Try ISO format
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        bucket = dt.strftime("%Y-%m-%d %H:") + f"{(dt.minute // window_minutes) * window_minutes:02d}"
        total_buckets[bucket] += 1
        if e.get("level") in ("ERROR", "FATAL", "CRITICAL"):
            error_buckets[bucket] += 1

    if not error_buckets:
        return json.dumps({"anomalies": [], "message": "No timestamped errors found"})

    all_windows = sorted(set(total_buckets.keys()) | set(error_buckets.keys()))
    error_counts = np.array([error_buckets.get(w, 0) for w in all_windows])

    mean = float(error_counts.mean())
    std = float(error_counts.std()) if len(error_counts) > 1 else 0

    anomalies = []
    for i, window in enumerate(all_windows):
        count = int(error_counts[i])
        if std > 0 and count > mean + threshold_std * std:
            anomalies.append({
                "window": window,
                "error_count": count,
                "total_events": total_buckets.get(window, 0),
                "z_score": round((count - mean) / std, 2) if std > 0 else 0,
            })

    return json.dumps({
        "window_minutes": window_minutes,
        "threshold_std": threshold_std,
        "baseline_mean": round(mean, 2),
        "baseline_std": round(std, 2),
        "total_windows": len(all_windows),
        "anomalies": anomalies,
    }, indent=2)


@mcp.tool()
def incident_timeline(
    path: str,
    start_time: str,
    end_time: str,
    levels: list[str] | None = None,
) -> str:
    """Extract events within a time window, optionally filtered by level.
    Useful for building an incident timeline."""
    events = json.loads(_safe_path(path).read_text())
    if levels is None:
        levels = ["ERROR", "FATAL", "CRITICAL", "WARN", "WARNING"]

    levels_upper = {lv.upper() for lv in levels}
    timeline = []

    for e in events:
        ts = e.get("timestamp", "")
        if not ts:
            continue
        level = e.get("level", "").upper()
        if level not in levels_upper:
            continue
        if start_time <= ts <= end_time:
            timeline.append(e)

    return json.dumps({
        "start": start_time,
        "end": end_time,
        "levels": sorted(levels_upper),
        "events": timeline[:500],
        "total_matched": len(timeline),
    }, indent=2)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)

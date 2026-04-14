"""
Cooperage Example: DNA/Protein Sequence Analyzer (Biotech)

Parses FASTA files from /workspace, computes sequence statistics, searches
for motifs, and generates sequence composition charts. Handles multi-sequence
FASTA files with thousands of sequences.

Tools:
  parse_fasta       — parse a FASTA file, return sequence count and summary stats
  analyze_sequence  — compute GC content, length, amino acid/nucleotide composition
  search_motif      — find all occurrences of a motif pattern across sequences
  composition_chart — generate a bar chart of nucleotide/amino acid frequencies
"""

import io
import json
import os
import re
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import uvicorn
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("sequence-analyzer", json_response=True, stateless_http=True)


def _safe_path(filename: str) -> Path:
    resolved = (WORKSPACE / filename).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path {filename!r} escapes workspace")
    return resolved


def _parse_fasta_file(path: Path) -> list[dict]:
    """Parse a FASTA file into a list of {id, description, sequence} dicts."""
    sequences = []
    current_id = None
    current_desc = ""
    current_seq = []

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_id is not None:
                sequences.append({
                    "id": current_id,
                    "description": current_desc,
                    "sequence": "".join(current_seq),
                })
            parts = line[1:].split(None, 1)
            current_id = parts[0] if parts else "unknown"
            current_desc = parts[1] if len(parts) > 1 else ""
            current_seq = []
        else:
            current_seq.append(line.upper())

    if current_id is not None:
        sequences.append({
            "id": current_id,
            "description": current_desc,
            "sequence": "".join(current_seq),
        })

    return sequences


@mcp.tool()
def parse_fasta(path: str, output: str = "sequences.json") -> str:
    """Parse a FASTA file from /workspace. Returns summary stats and saves
    parsed sequences to a JSON file for downstream analysis."""
    sequences = _parse_fasta_file(_safe_path(path))

    lengths = [len(s["sequence"]) for s in sequences]

    # Save parsed sequences
    out_path = _safe_path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(sequences, indent=2))

    result = {
        "file": path,
        "saved_to": output,
        "sequence_count": len(sequences),
        "total_residues": sum(lengths),
        "min_length": min(lengths) if lengths else 0,
        "max_length": max(lengths) if lengths else 0,
        "mean_length": round(np.mean(lengths), 1) if lengths else 0,
        "ids": [s["id"] for s in sequences[:20]],
    }
    if len(sequences) > 20:
        result["ids_truncated"] = True

    return json.dumps(result, indent=2)


@mcp.tool()
def analyze_sequence(path: str, sequence_id: str | None = None) -> str:
    """Compute detailed statistics on sequences from a parsed FASTA JSON file.
    If sequence_id is provided, analyze just that sequence. Otherwise, analyze all."""
    data = json.loads(_safe_path(path).read_text())

    if sequence_id:
        data = [s for s in data if s["id"] == sequence_id]
        if not data:
            raise ValueError(f"Sequence {sequence_id!r} not found")

    results = []
    for seq_data in data[:50]:  # cap at 50 for response size
        seq = seq_data["sequence"]
        counts = Counter(seq)
        total = len(seq)

        analysis = {
            "id": seq_data["id"],
            "length": total,
            "composition": {k: v for k, v in sorted(counts.items())},
        }

        # Detect if DNA/RNA or protein
        bases = set("ATCGUN")
        is_nucleotide = total > 0 and sum(counts.get(b, 0) for b in bases) / total > 0.9

        if is_nucleotide:
            gc = counts.get("G", 0) + counts.get("C", 0)
            analysis["type"] = "nucleotide"
            analysis["gc_content"] = round(gc / total * 100, 2) if total else 0
            analysis["at_content"] = round(100 - analysis["gc_content"], 2)
        else:
            analysis["type"] = "protein"
            # Hydrophobic residues
            hydrophobic = sum(counts.get(aa, 0) for aa in "AILMFWVP")
            analysis["hydrophobic_pct"] = round(hydrophobic / total * 100, 2) if total else 0
            # Charged residues
            charged = sum(counts.get(aa, 0) for aa in "DEKRH")
            analysis["charged_pct"] = round(charged / total * 100, 2) if total else 0

        results.append(analysis)

    return json.dumps(results, indent=2)


@mcp.tool()
def search_motif(path: str, motif: str, max_results: int = 100) -> str:
    """Search for a motif pattern across all sequences. Supports regex.
    Returns locations of all matches."""
    data = json.loads(_safe_path(path).read_text())
    pattern = re.compile(motif, re.IGNORECASE)

    matches = []
    for seq_data in data:
        for m in pattern.finditer(seq_data["sequence"]):
            matches.append({
                "sequence_id": seq_data["id"],
                "start": m.start(),
                "end": m.end(),
                "matched": m.group(),
            })
            if len(matches) >= max_results:
                return json.dumps({
                    "motif": motif,
                    "matches": matches,
                    "total_found": f"{max_results}+ (truncated)",
                }, indent=2)

    return json.dumps({
        "motif": motif,
        "matches": matches,
        "total_found": len(matches),
    }, indent=2)


@mcp.tool()
def composition_chart(
    path: str,
    sequence_id: str | None = None,
    output: str = "composition.png",
) -> str:
    """Generate a bar chart of nucleotide or amino acid composition.
    If sequence_id is given, charts that sequence. Otherwise, charts aggregate."""
    data = json.loads(_safe_path(path).read_text())

    if sequence_id:
        data = [s for s in data if s["id"] == sequence_id]
        if not data:
            raise ValueError(f"Sequence {sequence_id!r} not found")

    # Aggregate composition across all sequences
    total_counts = Counter()
    for seq_data in data:
        total_counts.update(seq_data["sequence"])

    if not total_counts:
        raise ValueError("No sequence data to chart")

    # Sort by frequency
    items = sorted(total_counts.items(), key=lambda x: -x[1])
    labels = [x[0] for x in items]
    values = [x[1] for x in items]

    fig, ax = plt.subplots(figsize=(12, 5))
    colors = plt.cm.Set3(np.linspace(0, 1, len(labels)))
    ax.bar(labels, values, color=colors)
    ax.set_xlabel("Residue")
    ax.set_ylabel("Count")
    title = f"Composition: {sequence_id}" if sequence_id else f"Aggregate Composition ({len(data)} sequences)"
    ax.set_title(title)
    plt.tight_layout()

    out_path = _safe_path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return json.dumps({"chart": output, "residues": len(labels), "total_count": sum(values)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)

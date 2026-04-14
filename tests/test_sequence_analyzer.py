"""
Sequence Analyzer server tests — FASTA parsing, analysis, motif search, charts, path traversal.
"""

import importlib
import importlib.util
import json
import os

import pytest

FASTA_CONTENT = """\
>seq1 Test sequence 1
ATCGATCGATCG
>seq2 Test sequence 2
GCGCGCGCGCGC
>protein1 A protein
MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSH
"""


@pytest.fixture(autouse=True)
def seq_mod(tmp_path, monkeypatch):
    """Import sequence-analyzer server with COOPERAGE_WORKSPACE pointing at tmp_path."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "sequence_analyzer_server",
        os.path.join(os.path.dirname(__file__), "..", "example-servers", "sequence-analyzer", "server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ws(tmp_path):
    return tmp_path


@pytest.fixture
def fasta_file(ws):
    p = ws / "test.fasta"
    p.write_text(FASTA_CONTENT)
    return p


# ── parse_fasta ──────────────────────────────────────────────────────────────


def test_parse_fasta_sequence_count(seq_mod, ws, fasta_file):
    result = json.loads(seq_mod.parse_fasta("test.fasta", output="out.json"))
    assert result["sequence_count"] == 3
    assert "seq1" in result["ids"]
    assert "seq2" in result["ids"]
    assert "protein1" in result["ids"]


def test_parse_fasta_saves_json(seq_mod, ws, fasta_file):
    seq_mod.parse_fasta("test.fasta", output="parsed.json")
    out = ws / "parsed.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert len(data) == 3
    assert data[0]["id"] == "seq1"
    assert data[0]["sequence"] == "ATCGATCGATCG"


# ── analyze_sequence ─────────────────────────────────────────────────────────


def test_analyze_gc_content(seq_mod, ws, fasta_file):
    seq_mod.parse_fasta("test.fasta", output="seqs.json")
    result = json.loads(seq_mod.analyze_sequence("seqs.json", sequence_id="seq1"))
    assert len(result) == 1
    entry = result[0]
    assert entry["type"] == "nucleotide"
    # ATCGATCGATCG: 4G + 2C = 6 out of 12 => 50%
    assert entry["gc_content"] == 50.0


def test_analyze_detects_protein(seq_mod, ws, fasta_file):
    seq_mod.parse_fasta("test.fasta", output="seqs.json")
    result = json.loads(seq_mod.analyze_sequence("seqs.json", sequence_id="protein1"))
    assert len(result) == 1
    assert result[0]["type"] == "protein"
    assert "hydrophobic_pct" in result[0]
    assert "charged_pct" in result[0]


def test_analyze_detects_nucleotide(seq_mod, ws, fasta_file):
    seq_mod.parse_fasta("test.fasta", output="seqs.json")
    result = json.loads(seq_mod.analyze_sequence("seqs.json", sequence_id="seq2"))
    assert result[0]["type"] == "nucleotide"
    # GCGCGCGCGCGC is 100% GC
    assert result[0]["gc_content"] == 100.0


# ── search_motif ─────────────────────────────────────────────────────────────


def test_search_motif_finds_positions(seq_mod, ws, fasta_file):
    seq_mod.parse_fasta("test.fasta", output="seqs.json")
    result = json.loads(seq_mod.search_motif("seqs.json", motif="ATCG"))
    matches = result["matches"]
    # seq1 = ATCGATCGATCG -> ATCG at 0, 4, 8
    seq1_matches = [m for m in matches if m["sequence_id"] == "seq1"]
    positions = [m["start"] for m in seq1_matches]
    assert 0 in positions
    assert 4 in positions
    assert 8 in positions


def test_search_motif_no_match(seq_mod, ws, fasta_file):
    seq_mod.parse_fasta("test.fasta", output="seqs.json")
    result = json.loads(seq_mod.search_motif("seqs.json", motif="ZZZZZ"))
    assert result["total_found"] == 0


# ── composition_chart ────────────────────────────────────────────────────────


def test_composition_chart_creates_png(seq_mod, ws, fasta_file):
    seq_mod.parse_fasta("test.fasta", output="seqs.json")
    result = json.loads(seq_mod.composition_chart("seqs.json", output="chart.png"))
    assert (ws / "chart.png").exists()
    assert result["residues"] > 0
    assert result["total_count"] > 0


# ── _safe_path ───────────────────────────────────────────────────────────────


def test_safe_path_blocks_traversal(seq_mod):
    with pytest.raises(ValueError, match="escapes workspace"):
        seq_mod._safe_path("../../etc/passwd")

"""Tests for the Cooperage SDK — workspace helpers."""

from pathlib import Path

import pytest

from cooperage_sdk.workspace import Workspace


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Create a Workspace pointed at a temp directory."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    return Workspace()


# ── path ─────────────────────────────────────────────────────────────────────

def test_path_returns_full_path(ws, tmp_path):
    p = ws.path("file.txt")
    assert isinstance(p, Path)
    assert str(p).startswith(str(tmp_path))


def test_path_traversal_blocked(ws):
    with pytest.raises(ValueError, match="escapes the workspace"):
        ws.path("../../etc/passwd")


def test_path_read_write_roundtrip(ws):
    ws.path("hello.txt").write_text("hello world")
    assert ws.path("hello.txt").read_text() == "hello world"


def test_path_creates_subdirs(ws):
    p = ws.path("sub/dir/file.txt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("nested")
    assert ws.path("sub/dir/file.txt").read_text() == "nested"


# ── root ─────────────────────────────────────────────────────────────────────

def test_root_property(ws, tmp_path):
    assert ws.root == tmp_path


# ── exists ───────────────────────────────────────────────────────────────────

def test_exists(ws):
    assert not ws.exists("nope.txt")
    ws.path("yep.txt").write_text("here")
    assert ws.exists("yep.txt")


# ── list ─────────────────────────────────────────────────────────────────────

def test_list_empty_workspace(ws):
    assert ws.list() == []


def test_list_files(ws):
    ws.path("a.txt").write_text("a")
    ws.path("b.txt").write_text("b")
    sub = ws.path("sub/c.txt")
    sub.parent.mkdir(parents=True, exist_ok=True)
    sub.write_text("c")
    files = ws.list()
    assert "a.txt" in files
    assert "b.txt" in files
    assert "sub/c.txt" in files


def test_list_subdirectory(ws):
    ws.path("top.txt").write_text("top")
    sub = ws.path("sub/nested.txt")
    sub.parent.mkdir(parents=True, exist_ok=True)
    sub.write_text("nested")
    assert ws.list("sub") == ["sub/nested.txt"]


def test_list_nonexistent_subdir(ws):
    assert ws.list("nope") == []

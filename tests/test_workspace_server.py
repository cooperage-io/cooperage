"""
Workspace server tests — file I/O, path traversal protection, binary handling.
"""

import base64
import json
import os

import pytest


@pytest.fixture(autouse=True)
def workspace_dir(tmp_path, monkeypatch):
    """Point the workspace server at a temp directory."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    # Re-import to pick up the new WORKSPACE path
    import importlib
    import servers.workspace.server as ws
    importlib.reload(ws)
    yield tmp_path


def _server():
    import servers.workspace.server as ws
    return ws


# ── _safe_path ──────────────────────────────────────────────────────────────


def test_safe_path_normal():
    ws = _server()
    p = ws._safe_path("data/output.txt")
    assert str(p).endswith("data/output.txt")


def test_safe_path_rejects_traversal():
    ws = _server()
    with pytest.raises(ValueError, match="escapes workspace"):
        ws._safe_path("../../../etc/passwd")


def test_safe_path_rejects_absolute():
    ws = _server()
    with pytest.raises(ValueError, match="escapes workspace"):
        ws._safe_path("/etc/passwd")


def test_safe_path_rejects_double_dot_in_middle():
    ws = _server()
    with pytest.raises(ValueError, match="escapes workspace"):
        ws._safe_path("data/../../etc/passwd")


def test_safe_path_allows_dotfiles():
    ws = _server()
    p = ws._safe_path(".hidden/config")
    assert ".hidden" in str(p)


# ── workspace_write / workspace_read ────────────────────────────────────────


def test_write_and_read(workspace_dir):
    ws = _server()
    result = ws.workspace_write("hello.txt", "world")
    data = json.loads(result)
    assert data["written"] == "hello.txt"
    assert data["bytes"] == 5

    content = ws.workspace_read("hello.txt")
    assert content == "world"


def test_write_creates_parent_dirs(workspace_dir):
    ws = _server()
    ws.workspace_write("deep/nested/dir/file.txt", "content")
    assert (workspace_dir / "deep" / "nested" / "dir" / "file.txt").exists()


def test_write_overwrites(workspace_dir):
    ws = _server()
    ws.workspace_write("overwrite.txt", "first")
    ws.workspace_write("overwrite.txt", "second")
    content = ws.workspace_read("overwrite.txt")
    assert content == "second"


def test_read_nonexistent_raises(workspace_dir):
    ws = _server()
    with pytest.raises(FileNotFoundError):
        ws.workspace_read("nosuchfile.txt")


def test_read_truncates_large_files(workspace_dir):
    ws = _server()
    big = "x" * 300_000
    ws.workspace_write("big.txt", big)
    content = ws.workspace_read("big.txt")
    assert len(content) < 300_000
    assert "truncated" in content


# ── workspace_list ──────────────────────────────────────────────────────────


def test_list_empty(workspace_dir):
    ws = _server()
    assert ws.workspace_list() == []


def test_list_files(workspace_dir):
    ws = _server()
    ws.workspace_write("a.txt", "1")
    ws.workspace_write("b.txt", "2")
    ws.workspace_write("sub/c.txt", "3")
    files = ws.workspace_list()
    assert "a.txt" in files
    assert "b.txt" in files
    assert os.path.join("sub", "c.txt") in files


# ── workspace_delete ────────────────────────────────────────────────────────


def test_delete_file(workspace_dir):
    ws = _server()
    ws.workspace_write("doomed.txt", "bye")
    result = ws.workspace_delete("doomed.txt")
    data = json.loads(result)
    assert data["deleted"] == "doomed.txt"
    assert not (workspace_dir / "doomed.txt").exists()


def test_delete_nonexistent_raises(workspace_dir):
    ws = _server()
    with pytest.raises(FileNotFoundError):
        ws.workspace_delete("ghost.txt")


def test_delete_traversal_blocked(workspace_dir):
    ws = _server()
    with pytest.raises(ValueError, match="escapes workspace"):
        ws.workspace_delete("../../etc/passwd")


# ── workspace_write_binary ──────────────────────────────────────────────────


def test_write_and_read_binary(workspace_dir):
    ws = _server()
    raw = b"\x89PNG\r\n\x1a\nfakedata"
    b64 = base64.b64encode(raw).decode()
    result = ws.workspace_write_binary("image.png", b64)
    data = json.loads(result)
    assert data["bytes"] == len(raw)

    content = ws.workspace_read("image.png")
    parsed = json.loads(content)
    assert parsed["type"] == "binary"
    assert parsed["encoding"] == "base64"
    assert base64.b64decode(parsed["data"]) == raw

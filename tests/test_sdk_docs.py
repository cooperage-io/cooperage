"""Tests for cooperage_sdk.docs — register_docs helper."""

from unittest.mock import MagicMock

from cooperage_sdk.docs import register_docs


def test_register_docs_from_directory(tmp_path):
    """register_docs should register each file in the docs dir as a resource."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "quickstart.md").write_text("# Getting Started\n\nThis is the quickstart guide.")
    (docs_dir / "api-reference.md").write_text("# API Reference\n\nAll available tools.")

    mcp = MagicMock()
    # mcp.resource() returns a decorator
    mcp.resource.return_value = lambda fn: fn

    register_docs(mcp, str(docs_dir))

    assert mcp.resource.call_count == 2
    uris = [c.args[0] for c in mcp.resource.call_args_list]
    assert "docs://api-reference.md" in uris
    assert "docs://quickstart.md" in uris


def test_register_docs_uses_first_line_as_description(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text("# My Guide Title\n\nBody text here.")

    mcp = MagicMock()
    mcp.resource.return_value = lambda fn: fn

    register_docs(mcp, str(docs_dir))

    kwargs = mcp.resource.call_args_list[0].kwargs
    assert kwargs["description"] == "My Guide Title"


def test_register_docs_derives_name_from_filename(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "scene-types.md").write_text("Scene type reference")

    mcp = MagicMock()
    mcp.resource.return_value = lambda fn: fn

    register_docs(mcp, str(docs_dir))

    kwargs = mcp.resource.call_args_list[0].kwargs
    assert kwargs["name"] == "Scene Types"


def test_register_docs_nonexistent_directory(tmp_path):
    """Should silently no-op if directory doesn't exist."""
    mcp = MagicMock()
    register_docs(mcp, str(tmp_path / "nonexistent"))
    assert mcp.resource.call_count == 0


def test_register_docs_empty_directory(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    mcp = MagicMock()
    register_docs(mcp, str(docs_dir))
    assert mcp.resource.call_count == 0


def test_register_docs_nested_subdirectory(tmp_path):
    docs_dir = tmp_path / "docs"
    sub = docs_dir / "advanced"
    sub.mkdir(parents=True)
    (sub / "deep-dive.md").write_text("Advanced topic")

    mcp = MagicMock()
    mcp.resource.return_value = lambda fn: fn

    register_docs(mcp, str(docs_dir))

    uris = [c.args[0] for c in mcp.resource.call_args_list]
    assert "docs://advanced/deep-dive.md" in uris

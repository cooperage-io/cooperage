"""
Web Scraper server tests — URL scraping, output files, non-HTML, path traversal.
"""

import importlib
import importlib.util
import json
import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def scraper_mod(tmp_path, monkeypatch):
    """Import web-scraper server with COOPERAGE_WORKSPACE pointing at tmp_path."""
    monkeypatch.setenv("COOPERAGE_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "web_scraper_server",
        os.path.join(os.path.dirname(__file__), "..", "example-servers", "web-scraper", "server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ws(tmp_path):
    return tmp_path


def _mock_response(text, content_type="text/html", status_code=200):
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock()
    return resp


# ── scrape_url (HTML) ───────────────────────────────────────────────────────


def test_scrape_url_extracts_text_and_links(scraper_mod, ws, monkeypatch):
    html = """<html><head><title>Test Page</title></head>
    <body><p>Hello world</p><a href="https://example.com/page2">Link</a></body></html>"""
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _mock_response(html))

    result = json.loads(scraper_mod.scrape_url("https://example.com", output="out.json"))
    assert result["title"] == "Test Page"
    assert result["word_count"] > 0
    assert result["link_count"] >= 1


def test_scrape_url_creates_output_file(scraper_mod, ws, monkeypatch):
    html = "<html><body>Content</body></html>"
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _mock_response(html))

    scraper_mod.scrape_url("https://example.com", output="result.json")
    assert (ws / "result.json").exists()
    data = json.loads((ws / "result.json").read_text())
    assert "text" in data


# ── scrape_url (non-HTML) ──────────────────────────────────────────────────


def test_scrape_url_non_html(scraper_mod, ws, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _mock_response(
        "plain text content", content_type="text/plain"
    ))

    result = json.loads(scraper_mod.scrape_url("https://example.com/data.txt", output="plain.json"))
    assert "word_count" in result
    saved = json.loads((ws / "plain.json").read_text())
    assert saved["content_type"] == "text/plain"
    assert "plain text content" in saved["text"]


# ── _safe_path ──────────────────────────────────────────────────────────────


def test_safe_path_blocks_traversal(scraper_mod):
    with pytest.raises(ValueError, match="escapes workspace"):
        scraper_mod._safe_path("../../etc/passwd")

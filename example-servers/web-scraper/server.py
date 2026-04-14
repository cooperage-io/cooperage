"""
Cooperage Example: Web Scraper

Fetches web pages, extracts text content and links, saves results to
/workspace. Pairs well with csv-analyzer (to analyze extracted data)
and pdf-report (to compile findings).

Tools:
  scrape_url     — fetch a URL and extract text, links, and metadata
  scrape_table   — extract HTML tables from a URL and save as CSV
"""

import json
import os
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("web-scraper", json_response=True, stateless_http=True)


def _safe_path(filename: str) -> Path:
    resolved = (WORKSPACE / filename).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path {filename!r} escapes workspace")
    return resolved


class _TextExtractor(HTMLParser):
    """Simple HTML → text extractor."""

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.links = []
        self.title = ""
        self._in_title = False
        self._skip_tags = {"script", "style", "noscript"}
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)

    def handle_endtag(self, tag):
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        text = data.strip()
        if text:
            if self._in_title:
                self.title = text
            self.text_parts.append(text)


@mcp.tool()
def scrape_url(url: str, output: str = "scraped.json", max_length: int = 50000) -> str:
    """Fetch a URL, extract text content and links, save to /workspace.
    Returns a summary with title, word count, and link count."""
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        # Non-HTML: save raw text
        text = resp.text[:max_length]
        result = {
            "url": url,
            "content_type": content_type,
            "text": text,
            "length": len(resp.text),
        }
    else:
        parser = _TextExtractor()
        parser.feed(resp.text)

        text = "\n".join(parser.text_parts)[:max_length]
        base = url
        links = []
        for href in parser.links:
            full = urljoin(base, href)
            if full.startswith(("http://", "https://")):
                links.append(full)

        result = {
            "url": url,
            "title": parser.title,
            "text": text,
            "word_count": len(text.split()),
            "links": links[:100],
            "link_count": len(links),
        }

    out_path = _safe_path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

    return json.dumps({
        "saved_to": output,
        "title": result.get("title", ""),
        "word_count": result.get("word_count", len(result.get("text", "").split())),
        "link_count": result.get("link_count", 0),
    })


@mcp.tool()
def scrape_table(url: str, table_index: int = 0, output: str = "table.csv") -> str:
    """Extract an HTML table from a URL and save as CSV in /workspace.
    table_index selects which table (0 = first). Returns column names and row count."""
    try:
        import pandas as pd
    except ImportError:
        return json.dumps({"error": "pandas not installed — add to requirements.txt"})

    tables = pd.read_html(url)
    if not tables:
        return json.dumps({"error": f"No tables found at {url}"})
    if table_index >= len(tables):
        return json.dumps({"error": f"Only {len(tables)} tables found, requested index {table_index}"})

    df = tables[table_index]
    out_path = _safe_path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    return json.dumps({
        "saved_to": output,
        "rows": len(df),
        "columns": list(df.columns),
        "tables_found": len(tables),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)

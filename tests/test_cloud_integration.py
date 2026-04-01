"""
Cloud integration tests — make real HTTP/SSE calls to a live Cooperage gateway.

These tests are skipped by default. To run them:

    uv run pytest tests/test_cloud_integration.py -v -m cloud

Override the target URL with:

    COOPERAGE_CLOUD_URL=http://your-host:8080/mcp uv run pytest tests/test_cloud_integration.py -v -m cloud
"""
import json
import os

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CLOUD_URL = os.environ.get("COOPERAGE_CLOUD_URL", "http://137.184.119.104:8080/mcp")

pytestmark = pytest.mark.cloud


def _server_reachable(url: str) -> bool:
    probe = url.replace("/mcp", "/oidc-config")
    try:
        httpx.get(probe, timeout=5)
        return True
    except Exception:
        return False


skip_if_down = pytest.mark.skipif(
    not _server_reachable(CLOUD_URL),
    reason=f"Cloud gateway not reachable at {CLOUD_URL}",
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _text(result) -> str:
    return result.content[0].text


def _json(result):
    return json.loads(_text(result))


def _extract_session_id(result) -> str:
    """Extract session_id from create_session's text response."""
    text = _text(result)
    for line in text.split("\n"):
        if "session_id:" in line:
            return line.split("session_id:")[1].strip()
    raise ValueError(f"No session_id found in response: {text}")


# ── tests ─────────────────────────────────────────────────────────────────────

@skip_if_down
async def test_list_tools():
    """Gateway exposes all expected cooperage_* tools."""
    async with streamable_http_client(CLOUD_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {t.name for t in result.tools}
            assert "cooperage_list_servers" in names
            assert "cooperage_create_session" in names
            assert "cooperage_end_session" in names
            assert "cooperage_workspace_write" in names
            assert "cooperage_workspace_read" in names
            assert "cooperage_workspace_list" in names
            assert "cooperage_call_tool" in names


@skip_if_down
async def test_list_servers():
    """list_servers returns a list (may be empty if no servers registered)."""
    async with streamable_http_client(CLOUD_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("cooperage_list_servers", {})
            servers = _json(result)
            assert isinstance(servers, list)
            # __workspace__ and __compute__ are internal — they should not appear
            names = [s["name"] for s in servers]
            assert "__workspace__" not in names
            assert "__compute__" not in names


@skip_if_down
async def test_create_and_end_session():
    """Create a named session then end it; both calls succeed."""
    async with streamable_http_client(CLOUD_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            create_result = await session.call_tool(
                "cooperage_create_session", {"name": "integration-test"}
            )
            session_id = _extract_session_id(create_result)
            assert session_id

            end = _json(await session.call_tool(
                "cooperage_end_session", {"session_id": session_id}
            ))
            assert end["ended"] is True
            assert end["session_id"] == session_id


@skip_if_down
async def test_workspace_write_read_list():
    """Write a file, read it back, and confirm it appears in workspace_list.

    Uses a longer SSE timeout because the __workspace__ container may need
    to start from cold on the droplet (~30-60 s).
    """
    async with streamable_http_client(
        CLOUD_URL, http_client=httpx.AsyncClient(timeout=httpx.Timeout(120))
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            create_result = await session.call_tool(
                "cooperage_create_session", {"name": "ws-roundtrip"}
            )
            session_id = _extract_session_id(create_result)

            try:
                write_result = _text(await session.call_tool("cooperage_workspace_write", {
                    "session_id": session_id,
                    "path": "hello.txt",
                    "content": "hello from integration test",
                }))
                assert not write_result.startswith("Error"), f"workspace_write failed: {write_result}"

                read_result = _text(await session.call_tool("cooperage_workspace_read", {
                    "session_id": session_id,
                    "path": "hello.txt",
                }))
                assert "hello from integration test" in read_result

                list_result = _text(await session.call_tool(
                    "cooperage_workspace_list", {"session_id": session_id}
                ))
                assert "hello.txt" in list_result
            finally:
                await session.call_tool(
                    "cooperage_end_session", {"session_id": session_id}
                )

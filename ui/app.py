"""
Cooperage Workspace UI

Live viewer for active sessions, running containers, and /workspace file contents.
Run via: cooperage ui
"""

import json
import time

import httpx
import streamlit as st

st.set_page_config(
    page_title="Cooperage",
    page_icon="🛢",
    layout="wide",
)

# ── Config ────────────────────────────────────────────────────────────────────

GATEWAY_URL = st.sidebar.text_input("Gateway URL", value="http://localhost:8080/mcp")
AUTO_REFRESH = st.sidebar.slider("Auto-refresh (seconds)", 1, 30, 3)

st.title("🛢 Cooperage")

# ── MCP helpers ───────────────────────────────────────────────────────────────

def _call(method: str, params: dict) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    resp = httpx.post(GATEWAY_URL, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    return data.get("result", {})


def call_tool(tool_name: str, arguments: dict) -> str:
    result = _call("tools/call", {"name": tool_name, "arguments": arguments})
    content = result.get("content", [])
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(texts)


def list_sessions() -> list[dict]:
    from pathlib import Path
    sessions_file = Path.home() / ".cooperage" / "sessions.json"
    if not sessions_file.exists():
        return []
    try:
        data = json.loads(sessions_file.read_text())
        return [
            {
                "session_id": s["id"],
                "name": s.get("name"),
                "expires_at": s.get("expires_at", ""),
                "containers": [
                    {"server_name": name, "container_id": cid, "builtin": name.startswith("__")}
                    for name, cid in s.get("containers", {}).items()
                ],
            }
            for s in data
        ]
    except Exception:
        return []


def workspace_list(session_id: str) -> list[str]:
    raw = call_tool("cooperage_workspace_list", {"session_id": session_id})
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return [f for f in raw.strip().split("\n") if f]


def workspace_read(session_id: str, path: str) -> str:
    return call_tool("cooperage_workspace_read", {"session_id": session_id, "path": path})


# ── Layout ────────────────────────────────────────────────────────────────────

all_sessions = list_sessions()

if not all_sessions:
    st.info("No active sessions. Create one in Claude Desktop with `cooperage_create_session`.")
    time.sleep(AUTO_REFRESH)
    st.rerun()

# Session selector
session_names = {
    f"{s.get('name') or 'unnamed'} ({s['session_id'][:8]}...)": s['session_id']
    for s in all_sessions
}
selected_label = st.selectbox("Session", list(session_names.keys()))
session_id = session_names[selected_label]
session = next(s for s in all_sessions if s["session_id"] == session_id)

col_containers, col_files, col_preview = st.columns([1, 1, 2])

# ── Containers ────────────────────────────────────────────────────────────────

with col_containers:
    st.subheader("Containers")
    containers = session.get("containers", [])
    if not containers:
        st.caption("No containers running yet.")
    for c in containers:
        name = c["server_name"]
        cid = c["container_id"][:12]
        if c["builtin"]:
            st.success(f"**{name}** `{cid}`")
        else:
            st.info(f"**{name}** `{cid}`")
    st.caption("🟢 Built-in  🔵 Add-on")

# ── File tree ─────────────────────────────────────────────────────────────────

with col_files:
    st.subheader("Workspace")
    files = workspace_list(session_id)
    if not files:
        st.caption("Workspace is empty.")
        selected_file = None
    else:
        selected_file = st.radio("Files", files, label_visibility="collapsed")

# ── File preview ──────────────────────────────────────────────────────────────

with col_preview:
    st.subheader("Preview")
    if selected_file:
        try:
            content = workspace_read(session_id, selected_file)
            ext = selected_file.rsplit(".", 1)[-1].lower() if "." in selected_file else ""

            if ext in ("png", "jpg", "jpeg", "gif", "bmp"):
                import base64
                from pathlib import Path
                img_bytes = base64.b64decode(content) if not content.startswith("\x89PNG") else content.encode("latin-1")
                st.image(img_bytes)
            elif ext == "json":
                try:
                    st.json(json.loads(content))
                except Exception:
                    st.code(content)
            elif ext in ("md", "markdown"):
                st.markdown(content)
            else:
                st.code(content)
        except Exception as e:
            st.error(f"Could not read file: {e}")
    else:
        st.caption("Select a file to preview.")

# ── Auto-refresh ──────────────────────────────────────────────────────────────

time.sleep(AUTO_REFRESH)
st.rerun()

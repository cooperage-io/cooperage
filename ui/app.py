"""
Cooperage Workspace UI

Live viewer for active sessions, running containers, and /workspace file contents.
Run via: cooperage ui
"""

import base64
import io
import json
import tarfile

import httpx
import streamlit as st

st.set_page_config(
    page_title="Cooperage",
    page_icon="🪵",
    layout="wide",
)

# ── Config (outside fragment so sidebar widgets are always live) ───────────────

GATEWAY_URL = st.sidebar.text_input("Gateway URL", value="http://localhost:8080/mcp")
AUTO_REFRESH = st.sidebar.slider("Auto-refresh (seconds)", 1, 30, 1)

st.title("🪵 Cooperage")

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
    try:
        raw = call_tool("cooperage_list_sessions", {})
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, list):
            return []
        return data
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


def workspace_read(session_id: str, path: str, max_size: int = 32) -> str:
    return call_tool("cooperage_workspace_read", {"session_id": session_id, "path": path, "max_size": max_size})


def workspace_read_raw(session_id: str, path: str) -> tuple[bytes, str]:
    """Return (raw_bytes, mime_type) for a file — full resolution, no thumbnail."""
    raw = workspace_read(session_id, path, max_size=0)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("type") == "binary":
            return base64.b64decode(parsed["data"]), parsed.get("mime", "application/octet-stream")
    except Exception:
        pass
    return raw.encode("utf-8"), "text/plain"


@st.cache_data(ttl=30)
def workspace_download_all(session_id: str, files: tuple) -> bytes:
    """Fetch all workspace files and return a tar.gz archive as bytes. Cached for 30s."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in files:
            try:
                data, _ = workspace_read_raw(session_id, path)
                info = tarfile.TarInfo(name=path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            except Exception:
                pass
    return buf.getvalue()


# ── File tree helpers ─────────────────────────────────────────────────────────

def _build_tree(paths: list[str]) -> dict:
    tree: dict = {}
    for path in sorted(paths):
        parts = path.split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None
    return tree


def _render_tree(tree: dict, prefix: str = "") -> None:
    dirs = sorted((k, v) for k, v in tree.items() if isinstance(v, dict))
    files = sorted(k for k, v in tree.items() if v is None)
    for name, subtree in dirs:
        full_path = f"{prefix}/{name}" if prefix else name
        with st.expander(f"📁 {name}", expanded=False):
            _render_tree(subtree, prefix=full_path)
    for name in files:
        full_path = f"{prefix}/{name}" if prefix else name
        selected = st.session_state.get("selected_file") == full_path
        if st.button(
            name,
            key=f"tree__{full_path}",
            use_container_width=True,
            type="primary" if selected else "secondary",
        ):
            st.session_state["selected_file"] = full_path


# ── Main content (fragment refreshes without full-page flash) ─────────────────

@st.fragment(run_every=AUTO_REFRESH)
def main_content() -> None:
    all_sessions = list_sessions()

    if not all_sessions:
        st.info("No active sessions. Create one in Claude Desktop with `cooperage_create_session`.")
        return

    session_names = {
        f"{s.get('name') or 'unnamed'} ({s['session_id'][:8]}...)": s['session_id']
        for s in all_sessions
    }
    selected_label = st.selectbox("Session", list(session_names.keys()))
    session_id = session_names[selected_label]
    session = next(s for s in all_sessions if s["session_id"] == session_id)

    # Clear selected file when switching sessions
    if st.session_state.get("_session_id") != session_id:
        st.session_state["_session_id"] = session_id
        st.session_state["selected_file"] = None

    col_containers, col_files, col_preview = st.columns([1, 1, 2])

    with col_containers:
        st.subheader("Containers")
        containers = session.get("containers", [])
        if not containers:
            st.caption("No containers running yet.")
        for c in containers:
            if c.get("status") == "warming":
                st.warning(f"⏳ **{c['server_name']}** warming...")
            elif c["builtin"]:
                st.success(f"🟢 **{c['server_name']}** `{c['container_id'][:12]}`")
            else:
                st.info(f"🔵 **{c['server_name']}** `{c['container_id'][:12]}`")
        st.caption("🟢 Built-in  🔵 Add-on  ⏳ Warming")

    with col_files:
        st.subheader("Workspace")
        files = workspace_list(session_id)
        if not files:
            st.caption("Workspace is empty.")
            st.session_state["selected_file"] = None
        else:
            _render_tree(_build_tree(files))
            st.divider()
            st.download_button(
                "⬇️ Download workspace (.tar.gz)",
                data=workspace_download_all(session_id, tuple(files)),
                file_name="workspace.tar.gz",
                mime="application/gzip",
                use_container_width=True,
            )

    selected_file = st.session_state.get("selected_file")

    with col_preview:
        st.subheader("Preview")
        if selected_file:
            try:
                content = workspace_read(session_id, selected_file, max_size=0)
                ext = selected_file.rsplit(".", 1)[-1].lower() if "." in selected_file else ""

                binary = None
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and parsed.get("type") == "binary":
                        binary = parsed
                except Exception:
                    pass

                with st.container(height=500):
                    if binary is not None:
                        mime = binary.get("mime", "")
                        if mime.startswith("image/"):
                            import base64 as _b64
                            st.image(_b64.b64decode(binary["data"]))
                        else:
                            st.caption(f"Cannot preview .{ext} files")
                    elif ext == "json":
                        try:
                            st.json(json.loads(content))
                        except Exception:
                            st.code(content)
                    elif ext in ("md", "markdown"):
                        st.markdown(content)
                    else:
                        st.code(content)
                st.divider()
                dl_data, dl_mime = workspace_read_raw(session_id, selected_file)
                st.download_button(
                    f"⬇️ Download {selected_file.split('/')[-1]}",
                    data=dl_data,
                    file_name=selected_file.split("/")[-1],
                    mime=dl_mime,
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Could not read file: {e}")
        else:
            st.caption("Select a file to preview.")


main_content()

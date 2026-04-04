"""
Cooperage Workspace UI

Live viewer for active sessions, running containers, and /workspace file contents.
Run via: cooperage ui

Supports three auth modes:
  - No auth (local dev) — just works
  - API key / JWT — paste in the sidebar
  - SSO (OIDC) — redirects to your identity provider automatically
"""

import base64
import hashlib
import io
import json
import os
import secrets
import tarfile
from urllib.parse import urlencode

import httpx
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

_favicon = next((
    p for p in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "favicon.png"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon.png"),
    ] if os.path.exists(p)
), None)

st.set_page_config(
    page_title="Cooperage",
    page_icon=_favicon,
    layout="wide",
)


# ── Sidebar config ────────────────────────────────────────────────────────────

st.sidebar.title("⚙️ Settings")
GATEWAY_URL = st.sidebar.text_input("Gateway URL", value="http://localhost:8080/mcp")
AUTO_REFRESH = st.sidebar.slider("Auto-refresh (seconds)", 1, 30, 1)


# ── OIDC state helpers ────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _fetch_oidc_config(_base_url: str) -> dict | None:
    """Fetch OIDC config from the gateway. Cached for 60s since this rarely changes.
    _base_url is a cache key so it reruns if the gateway URL changes."""
    try:
        resp = httpx.get(f"{_base_url}/oidc-config", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _handle_oidc_callback() -> None:
    """Check for an OAuth2 authorization code in query params and exchange it for a token."""
    params = st.query_params
    code = params.get("code")
    if not code:
        return

    # Verify state to prevent CSRF
    returned_state = params.get("state", "")
    expected_state = st.session_state.get("_oidc_state", "")
    if returned_state != expected_state:
        st.error("SSO login failed: state mismatch. Please try again.")
        st.query_params.clear()
        return

    oidc = st.session_state.get("_oidc_config")
    if not oidc:
        st.query_params.clear()
        return

    # Exchange authorization code for tokens
    issuer = oidc["issuer_url"].rstrip("/")
    token_url = f"{issuer}/token"

    try:
        resp = httpx.post(token_url, data={
            "grant_type": "authorization_code",
            "client_id": oidc["client_id"],
            "code": code,
            "redirect_uri": _get_redirect_uri(),
            "code_verifier": st.session_state.get("_oidc_verifier", ""),
        }, timeout=10)
        resp.raise_for_status()
        tokens = resp.json()

        # Store the access token (prefer id_token for OIDC, fall back to access_token)
        token = tokens.get("id_token") or tokens.get("access_token")
        if token:
            st.session_state["_oidc_token"] = token
        # Clear PKCE state after successful exchange
        st.session_state.pop("_oidc_state", None)
        st.session_state.pop("_oidc_verifier", None)
        st.session_state.pop("_oidc_challenge", None)
    except Exception as e:
        st.error(f"SSO token exchange failed: {e}")

    st.query_params.clear()


def _get_redirect_uri() -> str:
    """Build the OAuth2 redirect URI pointing back to this Streamlit app.
    Uses the browser's current URL so it works in both local and deployed environments."""
    try:
        ctx = st.context
        headers = ctx.headers
        # Respect X-Forwarded headers from reverse proxies
        proto = headers.get("X-Forwarded-Proto", "http")
        host = headers.get("Host", "localhost:8501")
        return f"{proto}://{host}"
    except Exception:
        return "http://localhost:8501"


def _generate_pkce() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge pair."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _start_oidc_login(oidc: dict) -> str:
    """Build the OIDC authorization URL and store state for later verification.
    Reuses existing PKCE state if already generated (avoids invalidating
    the state on every Streamlit rerun before the user clicks the button)."""
    if "_oidc_state" not in st.session_state:
        st.session_state["_oidc_state"] = secrets.token_urlsafe(32)
        verifier, challenge = _generate_pkce()
        st.session_state["_oidc_verifier"] = verifier
        st.session_state["_oidc_challenge"] = challenge

    st.session_state["_oidc_config"] = oidc

    params = {
        "response_type": "code",
        "client_id": oidc["client_id"],
        "redirect_uri": _get_redirect_uri(),
        "scope": oidc["scopes"],
        "state": st.session_state["_oidc_state"],
        "code_challenge": st.session_state["_oidc_challenge"],
        "code_challenge_method": "S256",
    }
    return f"{oidc['authorization_endpoint']}?{urlencode(params)}"


# ── Auth sidebar ──────────────────────────────────────────────────────────────

def _render_auth_sidebar() -> None:
    """Render the authentication section in the sidebar."""
    oidc = _fetch_oidc_config(_gateway_base())

    with st.sidebar.expander("🔑 Authentication", expanded=not _get_token()):
        if oidc:
            # SSO is available
            if st.session_state.get("_oidc_token"):
                st.success("Signed in via SSO")
                if st.button("Sign out", use_container_width=True):
                    for key in ("_oidc_token", "_oidc_state", "_oidc_verifier", "_oidc_challenge", "_oidc_config"):
                        st.session_state.pop(key, None)
                    st.rerun()
            else:
                login_url = _start_oidc_login(oidc)
                st.link_button("🔒 Sign in with SSO", login_url, use_container_width=True)
                st.divider()
                st.caption("Or paste a token manually:")
                st.text_input("API key or JWT", type="password", key="auth_token")
        else:
            # No SSO — manual token only
            st.caption("Leave blank for local mode. Required when the gateway has auth enabled.")
            st.text_input("API key or JWT", type="password", key="auth_token")


def _get_token() -> str:
    """Return the current auth token — from SSO or manual entry."""
    return st.session_state.get("_oidc_token") or st.session_state.get("auth_token", "")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _gateway_base() -> str:
    """Derive the base gateway URL (without /mcp) for non-MCP endpoints."""
    if GATEWAY_URL.endswith("/mcp"):
        return GATEWAY_URL[:-4]
    return GATEWAY_URL.rstrip("/")


def _auth_headers() -> dict[str, str]:
    """Build Authorization header if a token is available."""
    token = _get_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _call(method: str, params: dict) -> dict:
    """Send a JSON-RPC request to the gateway."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **_auth_headers(),
    }
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


# ── Gateway connectivity check ───────────────────────────────────────────────

@st.cache_data(ttl=5)
def _check_gateway(_url: str, _token: str) -> str | None:
    """Return an error message if the gateway is unreachable, or None if OK.
    Cached for 5 seconds to avoid hammering the gateway on every fragment refresh.
    _url and _token are cache keys so the check reruns when config changes."""
    try:
        _call("tools/list", {})
        return None
    except httpx.ConnectError:
        return f"Cannot connect to gateway at `{GATEWAY_URL}`. Is it running?"
    except httpx.TimeoutException:
        return f"Gateway at `{GATEWAY_URL}` timed out."
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return "Authentication failed — check your credentials in the sidebar."
        return f"Gateway returned HTTP {e.response.status_code}."
    except Exception as e:
        return f"Gateway error: {e}"


# ── Session / workspace API ──────────────────────────────────────────────────

def list_sessions() -> list[dict]:
    raw = call_tool("cooperage_list_sessions", {})
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list):
        return []
    return data


def list_servers() -> list[dict]:
    raw = call_tool("cooperage_list_servers", {})
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list):
        return []
    return data


def fetch_container_logs(session_id: str, container_id: str, tail: int = 100) -> str:
    try:
        resp = httpx.get(
            f"{_gateway_base()}/logs/{session_id}/{container_id}",
            params={"tail": tail},
            headers=_auth_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("logs", "")
    except Exception as e:
        return f"(could not fetch logs: {e})"


def workspace_list(session_id: str) -> list[str]:
    try:
        raw = call_tool("cooperage_workspace_list", {"session_id": session_id})
    except Exception:
        return []  # container still warming up — next auto-refresh will retry
    if not raw:
        return []
    if raw.startswith("Error"):
        return []  # gateway returned an error (container not ready, etc.)
    try:
        return json.loads(raw)
    except Exception:
        return [f for f in raw.strip().split("\n") if f]


def workspace_read(session_id: str, path: str, max_size: int = 32) -> str:
    return call_tool("cooperage_workspace_read", {
        "session_id": session_id, "path": path, "max_size": max_size,
    })


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


# ── File preview ──────────────────────────────────────────────────────────────

def _render_preview(session_id: str, selected_file: str) -> None:
    """Render the file preview panel for the selected file."""
    try:
        content = workspace_read(session_id, selected_file, max_size=0)
        ext = selected_file.rsplit(".", 1)[-1].lower() if "." in selected_file else ""

        # Parse once — detect binary (base64-encoded) vs text content
        binary = None
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and parsed.get("type") == "binary":
                binary = parsed
        except Exception:
            pass

        # Resolve raw bytes once for both preview and download
        if binary is not None:
            dl_data = base64.b64decode(binary["data"])
            dl_mime = binary.get("mime", "application/octet-stream")
        else:
            dl_data = content.encode("utf-8")
            dl_mime = "text/plain"

        with st.container(height=500):
            if binary is not None:
                if dl_mime.startswith("image/"):
                    st.image(dl_data)
                elif dl_mime == "application/pdf":
                    b64_pdf = base64.b64encode(dl_data).decode()
                    components.html(
                        f'<embed src="data:application/pdf;base64,{b64_pdf}" '
                        f'width="100%" height="480px" type="application/pdf">',
                        height=490, scrolling=False,
                    )
                else:
                    st.caption(f"Cannot preview .{ext} files")
            elif ext in ("html", "htm", "svg"):
                components.html(content, height=480, scrolling=True)
            elif ext in ("csv", "tsv"):
                sep = "\t" if ext == "tsv" else ","
                try:
                    df = pd.read_csv(io.StringIO(content), sep=sep)
                    st.dataframe(df, use_container_width=True)
                except Exception:
                    st.code(content)
            elif ext == "pdf":
                # Text-mode PDF (shouldn't happen, but handle gracefully)
                st.code(content)
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
        st.download_button(
            f"⬇️ Download {selected_file.split('/')[-1]}",
            data=dl_data,
            file_name=selected_file.split("/")[-1],
            mime=dl_mime,
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"Could not read file: {e}")


# ── Main content (fragment refreshes without full-page flash) ─────────────────

def _render_servers_sidebar() -> None:
    """Render registered-servers sidebar (must run outside fragment)."""
    error = _check_gateway(GATEWAY_URL, _get_token())
    if error is not None:
        return

    try:
        all_sessions = list_sessions()
    except Exception:
        return

    try:
        servers = list_servers()
    except Exception:
        servers = []

    if not servers:
        return

    # Determine running servers from the selected session (if any)
    running_servers: set[str] = set()
    session_id = st.session_state.get("_session_id")
    if all_sessions and session_id:
        session = next((s for s in all_sessions if s["session_id"] == session_id), None)
        if session:
            running_servers = {c["server_name"] for c in session.get("containers", [])}

    with st.sidebar.expander("📦 Registered Servers", expanded=True):
        for srv in servers:
            name = srv["name"]
            cached = "✅" if srv.get("cached") else "⬇️"
            is_running = name in running_servers

            col_name, col_btn = st.columns([3, 1])
            with col_name:
                if is_running:
                    st.markdown(f"🟢 **{name}**")
                else:
                    st.markdown(f"{cached} **{name}**")
                if srv.get("description"):
                    st.caption(srv["description"])
            with col_btn:
                if is_running:
                    st.caption("Running")
                elif st.button("Start", key=f"start_srv__{name}", use_container_width=True):
                    if session_id:
                        with st.spinner(f"Starting {name}..."):
                            try:
                                call_tool("cooperage_list_tools", {
                                    "session_id": session_id,
                                    "server_name": name,
                                })
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to start {name}: {e}")


@st.fragment(run_every=AUTO_REFRESH)
def main_content() -> None:
    # Check gateway connectivity first
    error = _check_gateway(GATEWAY_URL, _get_token())
    if error is not None:
        st.error(error)
        return

    try:
        all_sessions = list_sessions()
    except Exception as e:
        st.error(f"Failed to list sessions: {e}")
        return

    if not all_sessions:
        st.info("No active sessions. Create one in Claude Desktop with `cooperage_create_session`.")
        return

    session_names = {
        f"{s.get('name') or 'unnamed'} ({s['session_id'][:8]}...)": s["session_id"]
        for s in all_sessions
    }
    sel_col, end_col = st.columns([4, 1])
    with sel_col:
        selected_label = st.selectbox("Session", list(session_names.keys()))
    session_id = session_names[selected_label]
    session = next(s for s in all_sessions if s["session_id"] == session_id)
    with end_col:
        st.markdown("<div style='height: 1.65rem'></div>", unsafe_allow_html=True)
        if st.button("🛑 End Session", use_container_width=True, type="secondary"):
            try:
                call_tool("cooperage_end_session", {"session_id": session_id})
                st.session_state["_session_id"] = None
                st.session_state["selected_file"] = None
                st.session_state["_selected_container"] = None
                st.session_state["_selected_container_name"] = None
                st.rerun()
            except Exception as e:
                st.error(f"Failed to end session: {e}")

    # Clear selections when switching sessions
    if st.session_state.get("_session_id") != session_id:
        st.session_state["_session_id"] = session_id
        st.session_state["selected_file"] = None
        st.session_state["_selected_container"] = None
        st.session_state["_selected_container_name"] = None

    col_containers, col_files, col_preview = st.columns([1, 1, 2])

    # ── Containers panel ──────────────────────────────────────────────────────
    with col_containers:
        st.subheader("Containers")
        containers = session.get("containers", [])
        st.markdown(
            "<style>div[data-testid='stButton'] button p { text-align: left; width: 100%; }</style>",
            unsafe_allow_html=True,
        )
        if not containers:
            st.caption("No containers running yet.")
        for c in containers:
            cid = c.get("container_id")
            name = c["server_name"]
            is_selected = st.session_state.get("_selected_container") == cid

            if c.get("status") == "warming":
                st.warning(f"⏳ **{name}** warming...")
            elif cid:
                icon = "🟢" if c["builtin"] else "🔵"
                if st.button(
                    f"{icon} {name}",
                    key=f"ctr__{cid}",
                    use_container_width=True,
                    type="primary" if is_selected else "secondary",
                ):
                    if is_selected:
                        st.session_state["_selected_container"] = None
                    else:
                        st.session_state["_selected_container"] = cid
                        st.session_state["_selected_container_name"] = name
        st.markdown(
            "<small style='color:grey;'>🟢 Built-in<br>🔵 Add-on<br>⏳ Warming</small>",
            unsafe_allow_html=True,
        )

        # ── Log viewer ────────────────────────────────────────────────────────
        selected_cid = st.session_state.get("_selected_container")
        if selected_cid:
            cname = st.session_state.get("_selected_container_name", selected_cid[:12])
            st.divider()
            st.markdown(f"**Terminal — {cname}** `{selected_cid[:12]}`")
            tail = st.select_slider(
                "Lines", options=[50, 100, 200, 500], value=100,
                key="log_tail",
            )
            logs = fetch_container_logs(session_id, selected_cid, tail=tail)
            st.code(logs, language="text")

    # ── Workspace panel ───────────────────────────────────────────────────────
    with col_files:
        st.subheader("Workspace")
        files = workspace_list(session_id)
        if not files:
            st.caption("Workspace is empty — files will appear here once containers write to /workspace.")
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

        with st.expander("⬆️ Upload file"):
            uploaded = st.file_uploader("Choose file", key=f"uploader__{session_id}")
            if uploaded is not None:
                dest = st.text_input(
                    "Save as",
                    value=uploaded.name,
                    key=f"upload_dest__{session_id}",
                )
                if st.button("Upload", key=f"upload_btn__{session_id}", use_container_width=True):
                    try:
                        resp = httpx.post(
                            f"{_gateway_base()}/upload/{session_id}",
                            params={"path": dest},
                            content=uploaded.getvalue(),
                            headers={
                                "Content-Type": "application/octet-stream",
                                **_auth_headers(),
                            },
                            timeout=60,
                        )
                        resp.raise_for_status()
                        st.success(f"Uploaded to {dest}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Upload failed: {e}")

    # ── Preview panel ─────────────────────────────────────────────────────────
    selected_file = st.session_state.get("selected_file")

    with col_preview:
        st.subheader("Preview")
        if selected_file:
            _render_preview(session_id, selected_file)
        else:
            st.caption("Select a file to preview.")


# ── App entry ─────────────────────────────────────────────────────────────────

# Handle OIDC callback (if returning from SSO redirect)
_handle_oidc_callback()

# Render auth sidebar
_here = os.path.dirname(os.path.abspath(__file__))
_logo_path = next((
    p for p in [
        os.path.join(_here, "..", "assets", "logo.png"),  # local dev
        os.path.join(_here, "logo.png"),                  # Docker
    ] if os.path.exists(p)
), None)
del _here
if _logo_path:
    import base64 as _b64
    with open(_logo_path, "rb") as _f:
        _logo_b64 = _b64.b64encode(_f.read()).decode()
    st.markdown(
        f'<img src="data:image/png;base64,{_logo_b64}" style="width:320px;height:auto;">',
        unsafe_allow_html=True,
    )
    del _logo_b64
else:
    st.title("Cooperage")
_render_auth_sidebar()

# Sidebar servers (outside fragment to avoid widget-in-external-container error)
_render_servers_sidebar()

# Main content
main_content()

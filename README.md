<p align="center">
  <img src="assets/logo.png" alt="Cooperage" width="420">
</p>

---

MCP makes it easy to give LLMs tools. Cooperage makes those tools *scalable* — each tool call runs in an isolated container with dedicated compute and a shared workspace volume. No infra to manage. Just register a Docker image and let your LLM orchestrate it.

## Why this exists

Writing an MCP server is easy. Deploying it somewhere an LLM can actually *use* it — for stateful runs, large datasets, multi-step pipelines — is not. Cooperage is the missing layer between "I have tools" and "my LLM can run them at scale on my own infrastructure."

The key thing that makes Cooperage different: **multiple containers per session, one shared `/workspace` volume.** A generator writes a file, an analyzer reads it, a report writer summarizes it — all orchestrated by the LLM, all passing data through the same volume. No other platform does this.

## Architecture

```
LLM (Claude Desktop / API)
       │  MCP (stdio or HTTP)
       ▼
┌─────────────────────────┐
│    Cooperage Gateway    │  ← single MCP server the LLM talks to
└────────────┬────────────┘
             │  HTTP JSON-RPC
     ┌───────┴───────┐
     ▼               ▼
[Container A]   [Container B]     ephemeral containers,
 your MCP server  your MCP server   spun up per session
     └───────┬───────┘
      shared /workspace volume    data persists across calls
```

## Quick start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker Desktop running.

```bash
# Install
git clone https://github.com/cooperage-io/cooperage.git && cd cooperage
uv sync

# Build the example server
docker buildx build --load -t cooperage-image-analyzer:latest example-servers/image-analyzer/

# Register it
uv run cooperage register \
  --name image-analyzer \
  --image cooperage-image-analyzer:latest \
  --description "Analyze images and data in /workspace using numpy/PIL"
```

### Connect to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cooperage": {
      "command": "/Users/YOUR_USERNAME/.local/bin/uv",
      "args": ["--directory", "/path/to/cooperage", "run", "--no-active", "cooperage", "start"]
    }
  }
}
```

Restart Claude Desktop. You should see the hammer icon — Cooperage is connected.

### HTTP mode (for programmatic access)

```bash
uv run cooperage start --sse
```

This starts the gateway on `http://localhost:8080/mcp` as a Streamable HTTP MCP server.

## What the LLM sees

These are the tools exposed to the LLM via MCP:

| Tool | What it does |
|------|-------------|
| `cooperage_list_servers` | List available servers and whether their images are cached. |
| `cooperage_pull_server` | Pre-pull a server image to avoid cold-start latency. |
| `cooperage_create_session` | Create a workspace session. Returns a `session_id`. |
| `cooperage_list_tools` | List tools on a server. Starts the container if needed. |
| `cooperage_call_tool` | Call a tool on a server. Starts the container if needed. |
| `cooperage_workspace_read` | Read a file from `/workspace`. Handles binary + base64. |
| `cooperage_workspace_write` | Write a file to `/workspace`. |
| `cooperage_workspace_list` | List files in `/workspace`. |
| `cooperage_run_script` | Run a Python script in the compute container. |
| `cooperage_run_bash` | Run a bash script in the compute container. |
| `cooperage_end_session` | Tear down containers and delete the workspace volume. |

The gateway also exposes [MCP Resources](https://modelcontextprotocol.io/docs/concepts/resources) for reading registry and session state programmatically.

## Multi-container pipelines

This is the core use case. Containers in the same session share `/workspace`:

```
cooperage_call_tool(session, "scene-generator", "generate", {type: "urban"})
  → container A starts, writes /workspace/scene.png

cooperage_call_tool(session, "image-analyzer", "analyze", {path: "scene.png"})
  → container B starts, reads /workspace/scene.png

cooperage_workspace_read(session, "analysis.json")
  → LLM reads the result directly
```

Two containers. One session. One shared volume. Your proprietary tools stay in your Docker images, on your infrastructure.

## Writing your own server

Package any MCP server as a Docker image:

1. Expose MCP on port `8000` (configurable at registration)
2. Use `StreamableHTTPSessionManager` with `json_response=True, stateless=True`
3. Read/write `/workspace` for cross-container data sharing

See [example-servers/image-analyzer/](example-servers/image-analyzer/) for a working example.

```bash
uv run cooperage register \
  --name my-server \
  --image my-org/my-server:latest \
  --description "Does the thing" \
  --repo-url https://github.com/my-org/my-server  # optional — lets the LLM clone and debug
```

## Kubernetes backend

Cooperage has a drop-in Kubernetes backend. Containers run as Pods with NodePort Services; the shared workspace uses `hostPath` volumes (or a ReadWriteMany PVC on multi-node clusters).

```bash
# Bootstrap the namespace
COOPERAGE_ORCHESTRATOR=kubernetes uv run cooperage init-k8s

# Start the gateway
COOPERAGE_ORCHESTRATOR=kubernetes uv run cooperage start
```

The tools and LLM config are identical — only the backend changes.

## Web UI

Cooperage ships with a Streamlit-based dashboard for monitoring sessions, viewing container status, and browsing workspace files.

```bash
uv run cooperage ui
```

Supports file preview (images, HTML, CSV, PDF) and file upload. When SSO is configured, the UI shows a login button automatically.

## Multi-tenant / enterprise mode

By default Cooperage runs in local mode — no auth, no quotas, everything works for a single user. For shared deployments, set `COOPERAGE_AUTH_ENABLED=true`.

**Authentication** (checked in order):

1. **API keys** — static keys mapped to tenants, with per-tenant RBAC and session quotas.
2. **HS256 JWT** — signed tokens with a `tenant_id` claim.
3. **OIDC / SSO** — RS256 tokens validated via JWKS from your identity provider (Okta, Azure AD, Auth0, etc.). The UI supports PKCE-based login — no client secret needed.

**Other enterprise features:**
- Per-container CPU and memory limits (default: 1 CPU, 512 MB)
- Per-session network isolation (Docker bridge networks / K8s NetworkPolicy)
- Private image registry authentication
- Session quotas per tenant
- Container idle timeout with automatic cleanup
- Session TTL extension on activity

## Configuration

All settings use the `COOPERAGE_` prefix and can go in a `.env` file. See [`.env.example`](.env.example).

| Variable | Default | Description |
|----------|---------|-------------|
| `COOPERAGE_ORCHESTRATOR` | `docker` | `docker` or `kubernetes` |
| `COOPERAGE_SESSION_TTL_SECONDS` | `1800` | Session auto-expiry |
| `COOPERAGE_CONTAINER_IDLE_TIMEOUT` | `600` | Stop idle containers after N seconds |
| `COOPERAGE_CONTAINER_STARTUP_TIMEOUT` | `120` | Seconds to wait for container readiness |
| `COOPERAGE_DEFAULT_CPU_LIMIT` | `1.0` | CPU limit per container |
| `COOPERAGE_DEFAULT_MEMORY_LIMIT` | `512m` | Memory limit per container |
| `COOPERAGE_NETWORK_ISOLATION` | `true` | Per-session network isolation |
| `COOPERAGE_AUTH_ENABLED` | `false` | Enable authentication |
| `COOPERAGE_API_KEYS_PATH` | — | Path to API keys JSON |
| `COOPERAGE_JWT_SECRET` | — | HS256 secret |
| `COOPERAGE_OIDC_ISSUER_URL` | — | OIDC issuer (e.g. Azure AD) |
| `COOPERAGE_OIDC_AUDIENCE` | — | Expected `aud` claim |
| `COOPERAGE_OIDC_CLIENT_ID` | — | OAuth2 client ID (enables SSO in UI) |
| `COOPERAGE_UI_URL` | — | UI base URL (shown to users after session creation) |
| `COOPERAGE_K8S_NAMESPACE` | `cooperage` | Kubernetes namespace |

## CLI

```
cooperage register      Register a Docker image as an MCP server
cooperage deregister    Remove a server from the registry
cooperage list-servers  List registered servers
cooperage sessions      List active sessions
cooperage start         Start the gateway (stdio default, --sse for HTTP)
cooperage init-k8s      Bootstrap the Cooperage K8s namespace
cooperage ui            Launch the web dashboard
```

## Comparison

| | Containers per session | Shared workspace | Infrastructure | Image source |
|-|------------------------|-----------------|----------------|--------------|
| **Cooperage** | Multiple (one per server) | Shared `/workspace` volume | Docker or Kubernetes | Any registry |
| **Manus** | Single VM | `/home/ubuntu` in one sandbox | Manus cloud only | Pre-installed runtimes |
| **Docker MCP Toolkit** | Multiple | Each container isolated | Docker Desktop only | Docker catalog |
| **AWS Bedrock AgentCore** | One (tools colocated) | Partial — 1 GB, preview | AWS only | ECR |
| **Google ADK + Vertex** | No container orchestration | Memory state only | GCP only | — |
| **Azure AI Foundry** | No container orchestration | Thread state only | Azure only | — |

### Cooperage vs Manus

Manus gives every agent a single pre-built Ubuntu VM with Python, Node.js, and Chromium. MCP servers are external API bridges — the agent calls out to them via `manus-mcp-cli`. It's a managed, general-purpose sandbox that works well for everyday tasks with zero setup.

Cooperage takes a different approach: there is no pre-built VM. The LLM dynamically spins up purpose-built containers, each running its own MCP server, all sharing a `/workspace` volume. MCP isn't a bridge to external services — it's the native interface between the LLM and the compute.

**Choose Manus** if you want a turnkey managed environment and don't need to control the infrastructure.

**Choose Cooperage** if you need:
- **Self-hosted / on-prem deployment** — your cluster, your network, your data
- **Custom runtimes** — CUDA, GDAL, proprietary SDKs, anything you can put in a Docker image
- **Multi-tool pipelines** — containers with different dependencies composing through `/workspace`
- **Enterprise auth and isolation** — OIDC, JWT, per-tenant RBAC, network policies, resource limits

For a deeper comparison, see [docs/cooperage-vs-manus.md](docs/cooperage-vs-manus.md).

## Tests

```bash
uv run pytest -v
```

## License

[MIT](LICENSE)

# Cooperage

**Where AI tools work together.**

MCP makes it easy to give LLMs tools. Cooperage makes those tools scalable — each tool call runs in an isolated Docker container with dedicated compute and a shared workspace volume. Spin up, run, tear down. No infra to manage.

## The problem

Writing an MCP server is easy. Deploying it somewhere your LLM can actually use it for real compute — stateful runs, large datasets, simulation pipelines — is hard. Cooperage is the missing layer.

## How it works

```
LLM (Claude Desktop / API)
        │  MCP (stdio)
        ▼
┌─────────────────────────┐
│    Cooperage Gateway      │  ← one MCP server the LLM connects to
└────────────┬────────────┘
             │  HTTP JSON-RPC
     ┌───────┴───────┐
     ▼               ▼
[Container A]   [Container B]   ← ephemeral Docker containers
 your MCP server  your MCP server  spun up on demand, per session
     └───────┬───────┘
      shared /workspace volume   ← data persists across calls in session
```

## Quick start

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker Desktop running

### 1. Install

```bash
git clone <repo> && cd cooperage
uv sync
```

### 2. Build the example server

```bash
docker buildx build --load -t cooperage-image-analyzer:latest example-servers/image-analyzer/
```

### 3. Register it

```bash
uv run cooperage register \
  --name image-analyzer \
  --image cooperage-image-analyzer:latest \
  --description "Analyze images and data in /workspace using numpy/PIL"
```

### 4. Add to Claude Desktop

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

Restart Claude Desktop. You'll see a hammer icon — Cooperage is connected.

### 5. Pre-warm (recommended before first use)

In Claude Desktop, ask:
> *"Pull the image-analyzer server so it's ready to use."*

Claude will call `cooperage_pull_server("image-analyzer")` to ensure the image is cached locally.

---

## Gateway tools

The LLM sees these tools:

| Tool | Description |
|------|-------------|
| `cooperage_list_servers` | List registered servers. Shows whether each image is cached locally. |
| `cooperage_pull_server(server_name)` | Pre-pull a server image. Call before creating a session to avoid cold-start latency. |
| `cooperage_create_session(name?)` | Create a workspace session. Returns `session_id`. All containers in this session share `/workspace`. |
| `cooperage_list_tools(session_id, server_name)` | List tools exposed by a server. Starts the container if needed. |
| `cooperage_call_tool(session_id, server_name, tool_name, arguments)` | Invoke a tool. Starts the container if needed. |
| `cooperage_end_session(session_id)` | Tear down all containers and delete the shared workspace volume. |

---

## Kubernetes backend

Cooperage ships with a Kubernetes orchestrator backend that is a drop-in replacement for the default Docker backend. Containers run as Pods with NodePort Services; the shared workspace is a `hostPath` volume (works on Docker Desktop K8s out of the box; use a PVC with a ReadWriteMany StorageClass for multi-node clusters).

### Setup

**1. Enable Kubernetes in Docker Desktop** — Settings → Kubernetes → Enable Kubernetes.

**2. Bootstrap the namespace:**
```bash
COOPERAGE_ORCHESTRATOR=kubernetes uv run cooperage init-k8s
```

**3. Start the gateway with the K8s backend:**
```bash
COOPERAGE_ORCHESTRATOR=kubernetes uv run cooperage start
```

Or add `COOPERAGE_ORCHESTRATOR=kubernetes` to `.env` to make it the default.

**4. Verify pods and services during a session:**
```bash
kubectl get pods -n cooperage
kubectl get svc -n cooperage
```

**5. Cleanup:**
```bash
kubectl delete namespace cooperage
```

The gateway tools and Claude Desktop config are identical — only the backend changes.

---

## CLI reference

```
cooperage register      Register a Docker image as an MCP server
cooperage list-servers  List registered servers
cooperage deregister    Remove a server from the registry
cooperage sessions      List active sessions
cooperage start         Start the gateway (stdio by default, --sse for HTTP)
cooperage init-k8s      Bootstrap the Cooperage namespace in Kubernetes
```

---

## Environment variables

See [`.env.example`](.env.example). Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `COOPERAGE_SESSION_TTL_SECONDS` | `1800` | Session auto-expiry (30 min) |
| `COOPERAGE_CONTAINER_STARTUP_TIMEOUT` | `30` | Seconds to wait for container readiness |
| `COOPERAGE_CONTAINER_PORT_RANGE_START` | `9000` | Host port range for containers (Docker) |
| `COOPERAGE_CONTAINER_PORT_RANGE_END` | `9999` | Host port range for containers (Docker) |
| `COOPERAGE_ORCHESTRATOR` | `docker` | Orchestrator backend: `docker` or `kubernetes` |
| `COOPERAGE_K8S_NAMESPACE` | `cooperage` | Kubernetes namespace |
| `COOPERAGE_K8S_NODE_PORT_RANGE_START` | `30000` | NodePort range start (K8s) |
| `COOPERAGE_K8S_NODE_PORT_RANGE_END` | `32767` | NodePort range end (K8s) |

---

## Writing your own MCP server

Package any MCP server as a Docker image. Requirements:

1. Expose an MCP server on port `8000` (configurable via `--port` at registration)
2. Use `StreamableHTTPSessionManager` with `json_response=True, stateless=True` so the gateway can POST to `/mcp`
3. Optionally read/write `/workspace` — it's a shared volume across your session

See [`example-servers/image-analyzer/`](example-servers/image-analyzer/) for a working example.

```bash
cooperage register \
  --name my-server \
  --image my-org/my-mcp-server:latest \
  --description "Does the thing" \
  --port 8000
```

---

## Landscape

There are a handful of platforms that touch this space. Here's an honest read on how they compare.

| | Containers per session | Shared workspace across servers | Infrastructure | Image source |
|-|------------------------|--------------------------------|----------------|--------------|
| **Cooperage** | Multiple (one per server) | ✅ Shared `/workspace` volume | Local Docker or Kubernetes | Any registry |
| **Docker MCP Toolkit** | Multiple (one per server) | ❌ Each container isolated | Local Docker Desktop only | Docker's curated catalog |
| **AWS Bedrock AgentCore** | One per session (agent + tools colocated) | Partial — `/mnt/workspace` inside one container, preview, 1 GB cap | AWS only | ECR |
| **Google ADK + Vertex AI Agent Engine** | No container orchestration for MCP servers (recommends Cloud Run separately) | ❌ Memory-based state only | GCP only | — |
| **Azure AI Foundry** | No container orchestration — connects to externally-hosted MCP endpoints | ❌ Thread state only | Azure only | — |
| **LangGraph Platform** | No — framework only, MCP servers hosted separately | ❌ In-memory/DB graph state | Local or SaaS | — |
| **Composio** | No — managed SaaS integrations, no user container control | ❌ Auth state only | SaaS only | — |

**The column that matters most for data pipelines:**

No other platform runs multiple MCP server containers within the same session and mounts them to a shared volume. AWS AgentCore is closest — it has per-session filesystem storage — but it colocates all tools inside a single container, runs only on AWS, and the feature is in preview with a 1 GB cap. Every cloud platform (Azure, Google, Composio) treats MCP servers as remote HTTP endpoints; container lifecycle is not their problem.

```
cooperage_call_tool(session_id, "synthetic-image-generator", "generate_scene", {scene_type: "urban"})
  → container A starts, writes /workspace/scene.png

cooperage_call_tool(session_id, "image-analyzer", "analyze_scene", {image_path: "scene.png"})
  → container B starts, reads /workspace/scene.png from the same volume
```

Two containers. One session. One shared volume. This is what enables LLM-orchestrated multi-stage pipelines — a generator, a solver, an analyzer, a report writer — each in its own isolated environment, passing data through the workspace. If your team has proprietary tools already packaged as Docker images, Cooperage is the layer that lets an LLM orchestrate them on your own infrastructure.

---

## Running tests

```bash
uv run pytest -v
```

85 tests, no Docker daemon or cluster required (all mocked).

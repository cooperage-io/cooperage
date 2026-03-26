# Cooperage

**Ephemeral container orchestration for MCP servers.**

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
docker buildx build --load -t cooperage-analysis:latest example-servers/analysis/
```

### 3. Register it

```bash
uv run cooperage register \
  --name analysis \
  --image cooperage-analysis:latest \
  --description "Run Python scripts with numpy/pandas, persist results to /workspace"
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
> *"Pull the analysis server so it's ready to use."*

Claude will call `cooperage_pull_server("analysis")` to ensure the image is cached locally.

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

## CLI reference

```
cooperage register      Register a Docker image as an MCP server
cooperage list-servers  List registered servers
cooperage deregister    Remove a server from the registry
cooperage sessions      List active sessions
cooperage start         Start the gateway (stdio by default, --sse for HTTP)
```

---

## Environment variables

See [`.env.example`](.env.example). Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `COOPERAGE_SESSION_TTL_SECONDS` | `1800` | Session auto-expiry (30 min) |
| `COOPERAGE_CONTAINER_STARTUP_TIMEOUT` | `30` | Seconds to wait for container readiness |
| `COOPERAGE_CONTAINER_PORT_RANGE_START` | `9000` | Host port range for containers |
| `COOPERAGE_CONTAINER_PORT_RANGE_END` | `9999` | Host port range for containers |

---

## Writing your own MCP server

Package any MCP server as a Docker image. Requirements:

1. Expose an MCP server on port `8000` (configurable via `--port` at registration)
2. Use `StreamableHTTPSessionManager` with `json_response=True, stateless=True` so the gateway can POST to `/mcp`
3. Optionally read/write `/workspace` — it's a shared volume across your session

See [`example-servers/analysis/`](example-servers/analysis/) for a working example.

```bash
cooperage register \
  --name my-server \
  --image my-org/my-mcp-server:latest \
  --description "Does the thing" \
  --port 8000
```

---

## Running tests

```bash
uv run pytest -v
```

55 tests, no Docker daemon required (all mocked).

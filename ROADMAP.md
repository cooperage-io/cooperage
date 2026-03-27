# Cooperage Roadmap

## Done

- [x] MCP gateway with core tools (list/pull/create/list_tools/call_tool/end/list_sessions)
- [x] Docker orchestrator — ephemeral containers, shared `/workspace` volume per session, TTL cleanup
- [x] Kubernetes orchestrator — drop-in backend, Pods + NodePort Services + hostPath workspace
- [x] `cooperage init-k8s` CLI command
- [x] `synthetic-image-generator` + `image-analyzer` example servers — multi-container demo verified on both backends
- [x] Multi-container demo verified on K8s backend (Docker Desktop)
- [x] Built-in workspace server — `cooperage_workspace_write/read/list/delete` gateway tools, auto-registered, pre-warmed on session create
- [x] Built-in compute server — `cooperage_run_script` (Python REPL) and `cooperage_run_bash` (bash), numpy/pandas/scipy/matplotlib/sklearn/pytest pre-installed, `uv` for live package installs
- [x] File-based session persistence — stdio (Claude Desktop) and SSE gateway share state via `~/.cooperage/sessions.json`
- [x] Workspace UI (`cooperage ui`) — live Streamlit viewer with session selector, container panel (color-coded builtin vs add-on), collapsible directory tree, file preview (images, JSON, markdown, code)
- [x] Image preview in workspace UI — binary files base64-encoded by workspace server, rendered in browser
- [x] 94 tests, all mocked
- [x] Landscape comparison vs Docker MCP Toolkit, AgentCore, ADK, Azure Foundry, LangGraph, Composio
- [x] GitHub org: cooperage-io, repo transferred

---

## Up Next

### 1. Cloud Demo Deploy (DigitalOcean)

**Goal:** A public URL that a customer can point Claude Desktop at for a demo.

#### Approach: single DigitalOcean droplet

One $20/mo VM running Docker. Gateway + server containers all on the same machine. Same Docker backend as local dev — no K8s needed at this stage.

```
Customer's Claude Desktop
        │  MCP over HTTP
        ▼
  your-ip:8080              ← gateway container (SSE mode)
        │
  [simulator] [analysis] [workspace] [compute]   ← sibling containers
        └──────────────────────────────────────┘
                     shared Docker volume
```

#### Prerequisites
- [ ] Create DigitalOcean account — $200 free credit for new accounts
- [ ] Create Docker Hub account — free, needed to push images
- [ ] Have SSH key ready (`~/.ssh/id_ed25519`)

#### Steps

**1. Create droplet**
- Ubuntu 24.04, Basic plan, 2 vCPU / 4GB RAM (~$24/mo)
- Add SSH key during creation, note the public IP

**2. Install Docker**
```bash
ssh root@<your-ip>
curl -fsSL https://get.docker.com | sh
```

**3. Push images to Docker Hub**
```bash
docker tag cooperage-workspace:latest <dockerhub>/cooperage-workspace:latest
docker tag cooperage-compute:latest   <dockerhub>/cooperage-compute:latest
docker tag cooperage-synthetic-image-generator:latest <dockerhub>/cooperage-synthetic-image-generator:latest
docker tag cooperage-image-analyzer:latest            <dockerhub>/cooperage-image-analyzer:latest
docker push <dockerhub>/cooperage-workspace:latest
docker push <dockerhub>/cooperage-compute:latest
docker push <dockerhub>/cooperage-synthetic-image-generator:latest
docker push <dockerhub>/cooperage-image-analyzer:latest

docker buildx build --load -t <dockerhub>/cooperage-gateway:latest .
docker push <dockerhub>/cooperage-gateway:latest
```

**4. Run the gateway on the droplet**
```bash
docker run -d \
  --name cooperage-gateway \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v cooperage-data:/root/.cooperage \
  <dockerhub>/cooperage-gateway:latest \
  cooperage start --sse --host 0.0.0.0 --port 8080
```

**5. Register servers**
```bash
docker exec cooperage-gateway cooperage register \
  --name synthetic-image-generator --image <dockerhub>/cooperage-synthetic-image-generator:latest \
  --description "Generate synthetic satellite imagery"

docker exec cooperage-gateway cooperage register \
  --name image-analyzer --image <dockerhub>/cooperage-image-analyzer:latest \
  --description "Analyze images and data from /workspace"
```

**6. Point Claude Desktop at it**
```json
{
  "mcpServers": {
    "cooperage": {
      "command": "/path/to/uv",
      "args": ["--directory", "/path/to/cooperage", "run", "--no-active", "cooperage", "start"]
    }
  }
}
```
_(Gateway URL in cooperage ui sidebar: `http://<your-ip>:8080/mcp`)_

#### Code changes needed
- Open port 8080 in DigitalOcean firewall
- Publish workspace/compute images to Docker Hub so the gateway can pull them without a local build

---

### 2. Publish built-in images to a registry

Right now `cooperage-workspace:latest` and `cooperage-compute:latest` are only available if you've built them locally. Anyone who `pip install cooperage` gets a broken gateway until they run `docker build` themselves.

**Fix:** Publish to Docker Hub or ghcr.io, update `_WORKSPACE_IMAGE` and `_COMPUTE_IMAGE` in `gateway/server.py` to use the full registry URL.

---

## Phase 4 — Auth + Multi-tenancy

**Goal:** Multiple teams/users can share one Cooperage deployment without seeing each other's sessions.

### 4a. API key auth on the gateway
- Config: `COOPERAGE_AUTH_ENABLED` (default `false`)
- Validate `Authorization: Bearer <api-key>` on every tool call
- `cooperage keys create/list/revoke`

### 4b. Session isolation per tenant
- Session store keyed by `(tenant_id, session_id)`
- Docker: container label `cooperage.tenant={tenant_id}`
- K8s: namespace-level RBAC

### 4c. Resource limits at registration
- `cooperage register --cpu 2 --memory 4Gi`
- Docker: `nano_cpus` + `mem_limit`
- K8s: `resources.limits`

---

## Later / Backlog

- **Multi-agent demo** — orchestrator agent creates a session and passes the `session_id` to parallel subagents, each calling a different registered server. Subagents coordinate through `/workspace` (no explicit inter-agent messaging needed). Validates concurrent tool calls against the same session and demonstrates Cooperage as a shared compute substrate for multi-agent pipelines. Good target framework: Claude Agent SDK or LangGraph.

- **Smarter tool-use nudging** — current `cooperage_run_script` / `cooperage_run_bash` descriptions tell the LLM "do NOT use this for domain-specific work", which is wrong in general. The real goal is: prefer registered servers when available, fall back to run_script otherwise. Need a softer nudge that doesn't discourage legitimate general-purpose use of the compute container.

- **Fly.io / Railway backend** — simpler cloud deploy alternative to K8s
- **Resource usage telemetry** — CPU/memory per session, exposed via gateway tool
- **K8s: persistent shared workspace** — replace hostPath with RWX PVC for multi-node clusters
- **K8s: private registry support** — `imagePullSecrets` config
- **K8s: gateway as a Deployment** — Helm chart, ServiceAccount + ClusterRole, ConfigMap
- **`cooperage deploy` CLI** — thin wrapper around Helm chart

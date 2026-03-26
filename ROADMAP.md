# Cooperage Roadmap

## Done

- [x] MCP gateway with 6 tools (list/pull/create/list_tools/call_tool/end)
- [x] Docker orchestrator — ephemeral containers, shared `/workspace` volume per session, TTL cleanup
- [x] Kubernetes orchestrator — drop-in backend, Pods + NodePort Services + hostPath workspace
- [x] `cooperage init-k8s` CLI command
- [x] Simulator + analysis example servers — multi-container demo verified on both backends
- [x] Multi-container demo verified on K8s backend (Docker Desktop)
- [x] Built-in workspace server — `cooperage_workspace_write/read/list` gateway tools, auto-registered, pre-warmed on session create
- [x] 94 tests, all mocked
- [x] Landscape comparison vs Docker MCP Toolkit, AgentCore, ADK, Azure Foundry, LangGraph, Composio

---

## Phase 3 — Cloud Demo Deploy

**Goal:** A public URL that a customer can point Claude Desktop at for a demo. Not production-scale — just always-on and accessible.

### Approach: single DigitalOcean droplet

One $20/mo VM running Docker. Gateway + server containers all on the same machine. Same Docker backend as local dev — no K8s needed at this stage. Fast to set up, easy to explain to a customer.

```
Customer's Claude Desktop
        │  MCP over HTTP
        ▼
  your-ip:8080              ← gateway container (SSE mode)
        │
  [simulator] [analysis] [workspace]   ← sibling containers on same VM
        └─────────────────┘
         shared Docker volume
```

### Prerequisites (do these first)
- [ ] Create DigitalOcean account (digitalocean.com) — $200 free credit for new accounts
- [ ] Create Docker Hub account (hub.docker.com) — free, needed to push images to the VM
- [ ] Have SSH key ready (already generated at `~/.ssh/id_ed25519`)

### Steps

**1. Create droplet**
- Ubuntu 24.04, Basic plan, 2 vCPU / 4GB RAM ($24/mo) — enough for demo workloads
- Add your SSH key (`~/.ssh/id_ed25519.pub`) during creation
- Note the droplet's public IP

**2. Install Docker on the droplet**
```bash
ssh root@<your-ip>
curl -fsSL https://get.docker.com | sh
```

**3. Push images to Docker Hub**
```bash
# Tag and push all three server images
docker tag cooperage-analysis:latest <your-dockerhub>/cooperage-analysis:latest
docker tag cooperage-simulator:latest <your-dockerhub>/cooperage-simulator:latest
docker tag cooperage-workspace:latest <your-dockerhub>/cooperage-workspace:latest
docker push <your-dockerhub>/cooperage-analysis:latest
docker push <your-dockerhub>/cooperage-simulator:latest
docker push <your-dockerhub>/cooperage-workspace:latest

# Build and push the gateway image
docker buildx build --load -t <your-dockerhub>/cooperage-gateway:latest .
docker push <your-dockerhub>/cooperage-gateway:latest
```

**4. Deploy on the droplet**
```bash
ssh root@<your-ip>
docker pull <your-dockerhub>/cooperage-gateway:latest
docker pull <your-dockerhub>/cooperage-analysis:latest
docker pull <your-dockerhub>/cooperage-simulator:latest
docker pull <your-dockerhub>/cooperage-workspace:latest

docker run -d \
  --name cooperage-gateway \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v cooperage-registry:/root/.cooperage \
  -e COOPERAGE_CONTAINER_PORT_RANGE_START=9000 \
  -e COOPERAGE_CONTAINER_PORT_RANGE_END=9999 \
  <your-dockerhub>/cooperage-gateway:latest \
  cooperage start --sse --host 0.0.0.0 --port 8080
```

**5. Register servers on the droplet**
```bash
docker exec cooperage-gateway cooperage register \
  --name analysis \
  --image <your-dockerhub>/cooperage-analysis:latest \
  --description "Run Python scripts with numpy/pandas"

docker exec cooperage-gateway cooperage register \
  --name simulator \
  --image <your-dockerhub>/cooperage-simulator:latest \
  --description "Generate synthetic satellite imagery"
```

**6. Point Claude Desktop at it**

Update `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "cooperage": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/client-stdio-to-http", "http://<your-ip>:8080/mcp"]
    }
  }
}
```
_(Or keep running locally and use the SSE gateway URL directly if the MCP client supports HTTP.)_

### Code changes needed
- `Dockerfile` (gateway): verify `cooperage start --sse` works as the container entrypoint — likely already works
- `docker-compose.yml`: update image references to use Docker Hub tags for cloud deploy
- Open port 8080 in DigitalOcean firewall settings

### Security note
Port 8080 will be publicly accessible. Fine for a demo, not for production. Add auth (Phase 4) before giving customers persistent access.

---

### 3a. Persistent shared workspace (replace hostPath)

hostPath works on single-node Docker Desktop K8s but breaks on multi-node clusters — each pod might land on a different node. Replace with a PVC backed by a ReadWriteMany StorageClass.

**Changes:**
- `kubernetes.py`: `create_volume` → create a `PersistentVolumeClaim` (RWX); `remove_volume` → delete the PVC
- `start_container` → mount the PVC instead of hostPath
- Config: `COOPERAGE_K8S_STORAGE_CLASS` (e.g. `efs-sc` on EKS, `filestore-rwx` on GKE)
- `remove_volume` cleanup pod becomes unnecessary — just delete the PVC

**Note:** StorageClass must support `ReadWriteMany`. EFS (EKS), Filestore (GKE), Azure Files (AKS) all work.

### 3b. Private registry support (imagePullSecrets)

Enterprise clusters pull images from private registries. K8s needs a pull secret to authenticate.

**Changes:**
- Config: `COOPERAGE_K8S_IMAGE_PULL_SECRET` (name of a pre-existing K8s Secret of type `kubernetes.io/dockerconfigjson`)
- `kubernetes.py` `start_container`: attach `image_pull_secrets` to Pod spec if set
- Docs: how to create the secret (`kubectl create secret docker-registry ...`)

### 3c. Gateway as a K8s Deployment

Right now the gateway runs as a local process. For cloud deployments it should run inside the cluster.

**Changes:**
- Helm chart (or raw manifests in `deploy/`) for:
  - `Deployment` for the gateway (SSE mode, `--sse`)
  - `Service` (ClusterIP or LoadBalancer)
  - `ServiceAccount` + `ClusterRole` with permissions to create/delete Pods, Services, PVCs in the `cooperage` namespace
  - `ConfigMap` for env vars
- Gateway registry needs to be cluster-persistent: swap `~/.cooperage/registry.json` for a K8s `ConfigMap` or a backing store (Postgres/Redis)

### 3d. `cooperage deploy` CLI command

Thin wrapper that applies the Helm chart / manifests to the current kubectl context.

```bash
cooperage deploy --context my-eks-cluster --storage-class efs-sc
```

---

## Phase 4 — Auth + Multi-tenancy

**Goal:** Multiple teams/users can share one Cooperage deployment without seeing each other's sessions or servers.

### 4a. API key authentication on the gateway

All MCP tool calls go through the gateway. Add auth at that layer.

**Changes:**
- Config: `COOPERAGE_AUTH_ENABLED` (default `false` for local dev)
- Gateway middleware: validate `Authorization: Bearer <api-key>` header on every tool call
- `cooperage keys create <name>` — generate an API key, store hashed in registry
- `cooperage keys list / revoke`
- Each key is scoped to a tenant ID

### 4b. Session isolation per tenant

Tenants must not see each other's sessions or containers.

**Changes:**
- Session store keyed by `(tenant_id, session_id)` — prevents cross-tenant session access
- K8s: container/pod names and labels include `tenant_id` — RBAC enforces namespace isolation
- Docker: container label `cooperage.tenant={tenant_id}`

### 4c. Server registry per tenant

Right now all sessions share one global registry. Tenants should each manage their own servers.

**Changes:**
- Registry scoped by tenant: `~/.cooperage/{tenant_id}/registry.json` (local) or per-tenant ConfigMap (K8s)
- `cooperage register` requires an API key (or a privileged admin key)

### 4d. Resource limits at registration

Cap CPU and memory per container at registration time so one tenant can't starve others.

**Changes:**
- `cooperage register --cpu 2 --memory 4Gi`
- Docker: `nano_cpus` + `mem_limit` on `containers.run`
- K8s: `resources.limits` on the container spec

---

## Later / Backlog

- **Publish images to a registry** — publish `cooperage-workspace:latest` (and example server images) to Docker Hub / ghcr.io; update `_WORKSPACE_IMAGE` in the gateway to use the full registry URL so `pip install cooperage` works without a manual image build
- **Fly.io / Railway backend** — alternative to K8s for simpler cloud deploys
- **`cooperage_list_sessions` tool** — expose active sessions to the LLM
- **Resource usage telemetry** — track CPU/memory per session, expose via gateway tool
- **Web UI** — session dashboard, server registry management, live container logs
- **Live workspace viewer** — real-time file tree of `/workspace` that updates as the agent writes files; click to preview (text inline, images rendered, JSON pretty-printed). Goes through the workspace MCP server so it works on both Docker and K8s backends. Makes the shared volume concept visceral for demos.

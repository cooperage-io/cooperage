# Cooperage Roadmap

## Done

- [x] MCP gateway with 6 tools (list/pull/create/list_tools/call_tool/end)
- [x] Docker orchestrator — ephemeral containers, shared `/workspace` volume per session, TTL cleanup
- [x] Kubernetes orchestrator — drop-in backend, Pods + NodePort Services + hostPath workspace
- [x] `cooperage init-k8s` CLI command
- [x] Simulator + analysis example servers — multi-container demo verified on both backends
- [x] 86 tests, all mocked
- [x] Landscape comparison vs Docker MCP Toolkit, AgentCore, ADK, Azure Foundry, LangGraph, Composio

---

## Phase 3 — Cloud Deploy

**Goal:** Run Cooperage on a real cloud K8s cluster (EKS, GKE, AKS), not just Docker Desktop.

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

- **Fly.io / Railway backend** — alternative to K8s for simpler cloud deploys
- **`cooperage_list_sessions` tool** — expose active sessions to the LLM
- **Resource usage telemetry** — track CPU/memory per session, expose via gateway tool
- **Web UI** — session dashboard, server registry management, live container logs

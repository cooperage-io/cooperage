# Cooperage Roadmap

## Done

### Core
- [x] MCP gateway with core tools — `list_servers`, `create_session`, `end_session`, `list_sessions`, `call_tool`, `list_tools`, `pull_server`
- [x] Docker orchestrator — ephemeral containers, shared `/workspace` volume per session, TTL + idle cleanup
- [x] Kubernetes orchestrator — drop-in backend, Pods + NodePort Services + hostPath workspace, `cooperage init-k8s`, pod affinity for multi-node
- [x] Built-in workspace server — `workspace_write/read/list/delete`, auto-registered, pre-warmed on session create
- [x] Built-in compute server — `run_script` (Python) and `run_bash`, numpy/pandas/scipy/matplotlib/sklearn pre-installed, `uv` for live package installs
- [x] File-based session persistence — stdio and SSE gateway share state via configurable path
- [x] Container idle timeout + session activity TTL extension
- [x] User-facing session expiry control — `cooperage_set_session_expiry` tool + UI dropdown, capped at 72h from creation
- [x] Network isolation — per-session bridge networks
- [x] Resource limits — CPU/memory configurable at registration and via config
- [x] `repo_url` on `ServerDef` — LLM can inspect server source when debugging

### Auth (extracted to [cooperage-enterprise](https://github.com/cooperage-io/cooperage-enterprise))
- [x] API key auth
- [x] HS256 JWT auth
- [x] OIDC / RS256 JWT auth (Azure AD, Okta, Auth0)
- [x] Per-tenant session isolation and RBAC (`allowed_servers`, `max_sessions`)
- [x] Audit logging (JSON-lines event log)
- [x] Plugin interface — open core ships with `AuthProvider` and `AuditSink` protocols

### Deployment
- [x] DigitalOcean droplet — gateway + UI running in production at `137.184.119.104`
- [x] All images published to `ghcr.io/cooperage-io/` via GitHub Actions CI
- [x] Hosted Streamlit UI — live session/container/workspace viewer at port 8501
- [x] `cooperage start --proxy <url>` — Claude Desktop (stdio) → remote cloud gateway bridge
- [x] `COOPERAGE_UI_URL` — gateway returns session-scoped UI link in `create_session` response
- [x] Enterprise Docker image — layers cooperage-enterprise on core image via explicit `entrypoint.py` (no plugin discovery magic)
- [x] `--api-key` / `COOPERAGE_API_KEY` — proxy mode auth for enterprise gateways

### Developer Experience
- [x] `cooperage ui` — local Streamlit viewer with session selector, container panel, file preview, upload
- [x] Image preview — binary files base64-encoded, rendered in browser
- [x] `simulator` + `analysis` example servers
- [x] 212 unit tests (159 core + 53 enterprise) + cloud integration test suite against live droplet
- [x] MIT license, cooperage-io GitHub org
- [x] Helm chart — gateway Deployment + ServiceAccount + Role + ConfigMap + Ingress + UI sidecar
- [x] `/health` endpoint — K8s liveness/readiness probes
- [x] Persistent gateway state — PVC for sessions.json + registry.json
- [x] Image registry prefix — `COOPERAGE_IMAGE_REGISTRY_PREFIX` for air-gapped clusters
- [x] CI — GitHub Actions for tests + image publishing

---

## Up Next

- **GPU support for K8s workloads** — allow MCP tool servers to request GPU resources on enterprise Kubernetes clusters.
  - Extend `ResourceLimits` model with `gpu: int | None` and `gpu_type: str | None` (default `nvidia.com/gpu`) fields.
  - Wire GPU limits into the Pod spec in `KubernetesOrchestrator.start_container` as extended resource requests/limits (e.g. `nvidia.com/gpu: "1"`).
  - Add configurable `tolerations` and `nodeSelector` to `ServerDef` so pods land on tainted GPU nodes.
  - Add `default_gpu_tolerations` to `Settings` / Helm `values.yaml` for cluster-wide defaults.
  - GPU-enabled tool server images use `nvidia/cuda` base images with CUDA libraries pre-installed; Cooperage handles scheduling.
  - Docker orchestrator: pass `--gpus` flag via `device_requests` in the Docker SDK for local dev parity.
  - Example server: `gpu-compute` — CUDA-aware compute server with PyTorch/JAX pre-installed, registered with `"resources": {"gpu": 1}`.
  - Enterprise considerations: quota enforcement per tenant (max GPUs), audit log entries for GPU allocation, idle timeout tuning for expensive GPU pods.

---

## Backlog

- **Fly.io / Railway backend** — simpler cloud alternative to K8s or bare Docker VM
- **Dynamic resource sizing** — today resource limits are static (global defaults or per-server overrides at registration). Add support for: resource usage telemetry (CPU/memory per container, exposed as a gateway tool or resource), right-sizing recommendations based on historical usage, and optional Kubernetes VPA (Vertical Pod Autoscaler) integration for containers that consistently over- or under-request.
- **Workspace storage limits** — workspace volume is currently unbounded (hostPath on K8s, Docker volume locally). Add configurable per-session storage quotas and surface usage in the UI and `cooperage_list_sessions` output.
- **`cooperage deploy` CLI** — thin wrapper to provision cloud infra (droplet or K8s) and deploy the stack
- **K8s: RWX PVC workspace** — optional alternative to hostPath + pod affinity for clusters with RWX StorageClass
- **K8s: Ingress support** — replace NodePort with Ingress/ClusterIP for production on-prem routing
- **Session sharing** — read-only `session_id` tokens so a user can watch a session without write access
- **Webhook / event stream** — push session/container lifecycle events to an external URL
- **Server search / lazy listing** — `cooperage_search_servers` tool that accepts a query and returns matching servers by name/description, so large registries don't flood the context window. `cooperage_list_servers` would return names only (no descriptions) by default, with descriptions opt-in via a flag.
- **Configurable output paths in example servers** — simulator should accept an `output_path` parameter instead of always overwriting `scene.png`. Prevents the copy-after-generate footgun in multi-step pipelines.
- **Structured error handling** — tools should return structured error responses (error code, message, partial results) instead of null/silent failures. Critical for enterprise use where tools may run for minutes before failing.
- **Provenance / artifact lineage** — extend audit log to record which tool wrote which workspace file, enabling full artifact traceability across multi-container pipelines.
- **Multi-agent demo** — orchestrator agent creates a session and passes `session_id` to parallel subagents, each calling a different server. Validates concurrent tool calls. Target framework: Claude Agent SDK.
- **`cooperage.io` domain + public landing page** — set up domain, simple landing page with live demo link and install instructions.
- **Audit log rotation** — cap audit log file size and keep N compressed backups (Python `RotatingFileHandler` or logrotate config). Currently grows unbounded.
- **Interactive terminal in UI** — web-based terminal (xterm.js) in the Streamlit dashboard that connects to a session's compute container. Enables users to run bash interactively without CLI access to the host. Requires a websocket endpoint on the gateway. `cooperage exec` CLI command as a simpler alternative for local Docker deployments.

# Cooperage Roadmap

## Done

### Core
- [x] MCP gateway with core tools — `list_servers`, `create_session`, `end_session`, `list_sessions`, `call_tool`, `list_tools`, `pull_server`
- [x] Docker orchestrator — ephemeral containers, shared `/workspace` volume per session, TTL + idle cleanup
- [x] Kubernetes orchestrator — drop-in backend, Pods + NodePort Services + hostPath workspace, `cooperage init-k8s`, pod affinity for multi-node
- [x] Built-in workspace server — `workspace_write/read/list/delete`, auto-registered, pre-warmed on session create
- [x] Built-in compute server — `run_script` (Python) and `run_bash` with timeout + output cap, numpy/pandas/scipy/matplotlib/sklearn pre-installed
- [x] File-based session persistence — stdio and SSE gateway share state via configurable path
- [x] Container idle timeout + session activity TTL extension
- [x] User-facing session expiry control — `cooperage_set_session_expiry` tool + UI dropdown, capped at 72h from creation
- [x] Network isolation — per-session bridge networks
- [x] Resource limits — CPU/memory configurable at registration and via config
- [x] `repo_url` on `ServerDef` — LLM can inspect server source when debugging
- [x] `cooperage_get_container_logs` — LLM can view container stdout/stderr with configurable tail
- [x] Universal adapter — `cooperage register --from config.yaml` wraps REST APIs inline (no container) and LangChain tools (adapter container)
- [x] Available servers shown in `create_session` response so LLM knows what's available immediately
- [x] Async jobs — `cooperage_submit_job` for background execution, `cooperage_job_status/result/cancel`, `cooperage_stop_container` for hard kill, UI jobs panel

### Auth (extracted to [cooperage-enterprise](https://github.com/cooperage-io/cooperage-enterprise))
- [x] API key auth with per-entry validation and zero-key warnings
- [x] HS256 JWT auth
- [x] OIDC / RS256 JWT auth (Azure AD, Okta, Auth0) with URL validation
- [x] Per-tenant session isolation and RBAC (`allowed_servers`, `max_sessions`)
- [x] Atomic session quota enforcement (under lock, no TOCTOU)
- [x] Audit logging (JSON-lines, thread-safe writes)
- [x] Plugin interface — open core ships with `AuthProvider` and `AuditSink` protocols
- [x] Config validation — warns on auth_enabled without methods, validates OIDC URL scheme

### Deployment
- [x] DigitalOcean droplet — gateway + UI running in production at `137.184.119.104`
- [x] All images published to `ghcr.io/cooperage-io/` via GitHub Actions CI
- [x] Hosted Streamlit UI — live session/container/workspace viewer at port 8501
- [x] `cooperage start --proxy <url>` — Claude Desktop (stdio) → remote cloud gateway bridge
- [x] `COOPERAGE_UI_URL` — gateway returns session-scoped UI link in `create_session` response
- [x] Enterprise Docker image — layers cooperage-enterprise on core image via explicit `entrypoint.py`
- [x] `--api-key` / `COOPERAGE_API_KEY` — proxy mode auth for enterprise gateways

### Security & Quality
- [x] XSS-safe HTML/SVG preview in UI (sandboxed iframe)
- [x] Workspace symlink protection (defense-in-depth)
- [x] REST adapter path parameter sanitization (URL encoding)
- [x] Graceful degradation on corrupted registry.json or sessions.json
- [x] Container readiness check via JSON-RPC POST (rejects non-MCP servers)
- [x] 230 unit tests (core) + 35 (enterprise) + cloud integration suite
- [x] Comprehensive security audit with 30 issues tracked, 18 fixed, 7 accepted with documentation

### Developer Experience
- [x] `cooperage ui` — local Streamlit viewer with session selector, container panel, file preview, upload
- [x] Image preview — binary files base64-encoded, rendered in browser
- [x] `simulator` + `analysis` example servers
- [x] MIT license, cooperage-io GitHub org
- [x] Helm chart — gateway Deployment + ServiceAccount + Role + ConfigMap + Ingress + UI sidecar, with `env`/`extraVolumes`/`extraVolumeMounts` for enterprise
- [x] Enterprise K8s deployment guide — step-by-step: image build, API keys secret, Helm values, OIDC, monitoring
- [x] `/health` endpoint — K8s liveness/readiness probes
- [x] Persistent gateway state — PVC for sessions.json + registry.json
- [x] Image registry prefix — `COOPERAGE_IMAGE_REGISTRY_PREFIX` for air-gapped clusters
- [x] CI — GitHub Actions for tests + image publishing
- [x] [cooperage-sdk](https://github.com/cooperage-io/cooperage-sdk) — `workspace`, `serve`, `serve_functions`, `register_docs` helpers

---

## Up Next

### 1. Audit log rotation
Cap audit log file size and rotate compressed backups.
- Use Python `RotatingFileHandler` pattern in `FileAuditSink`
- Configurable max size and backup count via env vars
- Prevents disk-full silent event loss (audit issue #30)

### 2. Server search / lazy listing
For deployments with many registered servers (enterprise with 50+ tools):
- `cooperage_search_servers` tool — accepts a query, returns matching servers by name/description
- `cooperage_list_servers` returns names only by default, descriptions opt-in via flag
- Prevents flooding the LLM context window with tool descriptions

### 3. `cooperage.io` landing page
- Simple page with positioning, demo video, install instructions
- Live demo link to the droplet UI
- Domain already purchased

---

## Backlog

### Infrastructure
- **GPU support for K8s workloads** — extend `ResourceLimits` with `gpu` field, wire into Pod spec, Docker `--gpus` for local dev, `gpu-compute` example server with PyTorch/CUDA, per-tenant GPU quota enforcement
- **K8s: RWX PVC workspace** — optional alternative to hostPath + pod affinity for multi-node clusters with RWX StorageClass
- **K8s: Ingress support** — replace NodePort with Ingress/ClusterIP for production on-prem routing
- **Fly.io / Railway backend** — simpler cloud alternative to K8s or bare Docker VM

### Features
- **Interactive terminal** — web-based terminal (xterm.js) in the Streamlit UI connected to a session's compute container for interactive debugging. Requires a websocket endpoint on the gateway. As a simpler alternative, document SSH/exec access: `docker exec -it cooperage-{session_id[:8]}-__compute__ bash` for local Docker, `kubectl exec` for K8s.
- **Session sharing** — read-only `session_id` tokens so a user can watch a session without write access
- **Webhook / event stream** — push session/container lifecycle events to an external URL
- **Provenance / artifact lineage** — extend audit log to record which tool wrote which workspace file, enabling full artifact traceability across multi-container pipelines
- **Multi-agent demo** — orchestrator agent creates a session and passes `session_id` to parallel subagents, each calling a different server. Target framework: Claude Agent SDK.

### Quality of Life
- **Workspace storage limits** — configurable per-session storage quotas, surface usage in UI and `list_sessions`
- **Dynamic resource sizing** — resource usage telemetry, right-sizing recommendations, optional K8s VPA integration
- **Configurable output paths in example servers** — simulator should accept `output_path` parameter instead of overwriting `scene.png`
- **Structured error handling** — tools return error code + message + partial results instead of silent failures

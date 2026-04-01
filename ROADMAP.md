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
- [x] Network isolation — per-session bridge networks
- [x] Resource limits — CPU/memory configurable at registration and via config
- [x] `repo_url` on `ServerDef` — LLM can inspect server source when debugging

### Auth
- [x] API key auth (`COOPERAGE_AUTH_ENABLED`, `Authorization: Bearer`)
- [x] HS256 JWT auth
- [x] OIDC / RS256 JWT auth (Azure AD, Okta, Auth0)
- [x] Per-tenant session isolation and RBAC (`allowed_servers`, `max_sessions`)

### Deployment
- [x] DigitalOcean droplet — gateway + UI running in production at `137.184.119.104`
- [x] All images published to `ghcr.io/cooperage-io/` via GitHub Actions CI
- [x] Hosted Streamlit UI — live session/container/workspace viewer at port 8501
- [x] `cooperage start --proxy <url>` — Claude Desktop (stdio) → remote cloud gateway bridge
- [x] `COOPERAGE_UI_URL` — gateway returns session-scoped UI link in `create_session` response

### Developer Experience
- [x] `cooperage ui` — local Streamlit viewer with session selector, container panel, file preview, upload
- [x] Image preview — binary files base64-encoded, rendered in browser
- [x] `simulator` + `analysis` example servers
- [x] 161 unit tests (mocked) + cloud integration test suite against live droplet
- [x] MIT license, cooperage-io GitHub org
- [x] Helm chart — gateway Deployment + ServiceAccount + Role + ConfigMap + Ingress + UI sidecar
- [x] `/health` endpoint — K8s liveness/readiness probes
- [x] Persistent gateway state — PVC for sessions.json + registry.json
- [x] Image registry prefix — `COOPERAGE_IMAGE_REGISTRY_PREFIX` for air-gapped clusters
- [x] CI — GitHub Actions for tests + image publishing
- [x] Audit logging — JSON-lines event log for tool calls, sessions, and container lifecycle (`COOPERAGE_AUDIT_LOG_PATH`)

---

## Up Next

(Prioritizing based on demo feedback and enterprise readiness)

---

## Backlog

- **Fly.io / Railway backend** — simpler cloud alternative to K8s or bare Docker VM
- **Resource usage telemetry** — CPU/memory per container, exposed as a gateway tool or resource
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

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COOPERAGE_", env_file=".env")

    # Docker
    docker_socket: str = "unix:///var/run/docker.sock"
    container_port_range_start: int = 9000
    container_port_range_end: int = 9999
    container_startup_timeout: int = 120  # seconds

    # Sessions
    session_ttl_seconds: int = 1800  # 30 minutes
    session_cleanup_interval: int = 60  # seconds
    container_idle_timeout: int = 600  # 10 min — stop idle containers to free resources
    session_extend_on_activity: bool = True  # reset TTL on each tool call

    # Registry
    registry_path: Path = Path.home() / ".cooperage" / "registry.json"

    # Session persistence
    sessions_path: Path = Path.home() / ".cooperage" / "sessions.json"

    # Jobs
    jobs_path: Path = Path.home() / ".cooperage" / "jobs.json"
    job_default_timeout: int = 14400  # 4 hours max for async jobs

    # Gateway
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8080
    ui_url: str | None = None  # e.g. "http://localhost:8501" — shown to users after session creation

    # Container workspace mount path
    workspace_mount: str = "/workspace"

    # Orchestrator backend
    orchestrator: str = "docker"  # "docker" | "kubernetes"

    # Kubernetes settings (only used when orchestrator="kubernetes")
    k8s_namespace: str = "cooperage"
    k8s_node_port_range_start: int = 30000
    k8s_node_port_range_end: int = 32767
    k8s_host_path_prefix: str = "/run/cooperage"

    # ── Image registry prefix (air-gapped / internal mirror) ────────────────
    image_registry_prefix: str = ""  # e.g. "registry.corp.internal/"

    # ── Resource limits (defaults for all containers) ────────────────────────
    default_cpu_limit: str = "1.0"  # Docker: CPU quota; K8s: resource limit
    default_memory_limit: str = "512m"  # e.g. "512m", "1g", "2g"

    # ── Network isolation ────────────────────────────────────────────────────
    network_isolation: bool = True  # Create per-session networks

    # ── Image registry auth ──────────────────────────────────────────────────
    registry_auth_path: Path | None = None  # Path to registry credentials JSON



settings = Settings()

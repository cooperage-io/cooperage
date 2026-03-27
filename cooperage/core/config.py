from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COOPERAGE_", env_file=".env")

    # Docker
    docker_socket: str = "unix:///var/run/docker.sock"
    container_port_range_start: int = 9000
    container_port_range_end: int = 9999
    container_startup_timeout: int = 60  # seconds

    # Sessions
    session_ttl_seconds: int = 1800  # 30 minutes
    session_cleanup_interval: int = 60  # seconds

    # Registry
    registry_path: Path = Path.home() / ".cooperage" / "registry.json"

    # Sessions
    sessions_path: Path = Path.home() / ".cooperage" / "sessions.json"

    # Gateway
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8080

    # Container workspace mount path
    workspace_mount: str = "/workspace"

    # Orchestrator backend
    orchestrator: str = "docker"  # "docker" | "kubernetes"

    # Kubernetes settings (only used when orchestrator="kubernetes")
    k8s_namespace: str = "cooperage"
    k8s_node_port_range_start: int = 30000
    k8s_node_port_range_end: int = 32767
    k8s_host_path_prefix: str = "/run/cooperage"


settings = Settings()

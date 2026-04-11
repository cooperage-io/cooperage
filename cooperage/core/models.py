from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, SecretStr
import uuid


class ResourceLimits(BaseModel):
    """CPU and memory limits for a container."""
    cpu: str | None = None  # e.g. "0.5", "1.0", "2.0"
    memory: str | None = None  # e.g. "256m", "512m", "1g"


class RegistryCredentials(BaseModel):
    """Credentials for pulling from a private image registry."""
    server: str  # e.g. "ghcr.io", "123456789.dkr.ecr.us-east-1.amazonaws.com"
    username: str
    password: SecretStr


class ServerDef(BaseModel):
    name: str
    image: str
    port: int = 8000
    description: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    registry_credentials: RegistryCredentials | None = None
    repo_url: str | None = None  # Source repo — LLM can clone to inspect tool code


class ContainerInfo(BaseModel):
    container_id: str
    server_name: str
    session_id: str
    host_port: int
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def mcp_url(self) -> str:
        return f"http://localhost:{self.host_port}"


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = "default"
    name: str | None = None
    volume_name: str = ""
    network_name: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    # server_name → container_id
    containers: dict[str, str] = Field(default_factory=dict)

    def model_post_init(self, __context) -> None:
        if not self.volume_name:
            self.volume_name = f"cooperage-session-{self.id}"
        if not self.network_name:
            self.network_name = f"cooperage-net-{self.id[:12]}"


# ── Async Jobs ───────────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"  # gateway restarted while job was running


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str
    tenant_id: str = "default"
    server_name: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_path: str | None = None  # workspace path where result is stored
    error: str | None = None

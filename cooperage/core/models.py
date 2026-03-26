from datetime import datetime, timezone
from pydantic import BaseModel, Field
import uuid


class ServerDef(BaseModel):
    name: str
    image: str
    port: int = 8000
    description: str = ""
    env: dict[str, str] = Field(default_factory=dict)


class ContainerInfo(BaseModel):
    container_id: str
    server_name: str
    session_id: str
    host_port: int

    @property
    def mcp_url(self) -> str:
        return f"http://localhost:{self.host_port}"


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str | None = None
    volume_name: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    # server_name → container_id
    containers: dict[str, str] = Field(default_factory=dict)

    def model_post_init(self, __context) -> None:
        if not self.volume_name:
            self.volume_name = f"cooperage-session-{self.id}"

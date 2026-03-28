"""Abstract base class for container orchestrators."""

from abc import ABC, abstractmethod
import random
import socket
import time
import logging

import httpx

from cooperage.core.models import ContainerInfo, ServerDef, Session

logger = logging.getLogger(__name__)


class Orchestrator(ABC):
    """Contract that all orchestrator backends must implement."""

    @abstractmethod
    def pull_image(self, image: str) -> str: ...

    @abstractmethod
    def image_exists(self, image: str) -> bool: ...

    @abstractmethod
    def create_volume(self, volume_name: str) -> None: ...

    @abstractmethod
    def remove_volume(self, volume_name: str) -> None: ...

    @abstractmethod
    def start_container(self, server_def: ServerDef, session: Session) -> ContainerInfo: ...

    @abstractmethod
    def stop_container(self, container_id: str) -> None: ...

    @abstractmethod
    def get_container_logs(self, container_id: str, tail: int = 50) -> str: ...

    def wait_until_ready(self, info: ContainerInfo, timeout: int | None = None) -> bool:
        """Poll the container's MCP endpoint until it responds or timeout."""
        from cooperage.core.config import settings
        deadline = time.monotonic() + (timeout or settings.container_startup_timeout)
        url = f"{info.mcp_url}/mcp"
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(url, timeout=1.0)
                if resp.status_code < 500:
                    return True
            except (httpx.RequestError, httpx.HTTPStatusError):
                pass
            time.sleep(0.5)
        return False

    @staticmethod
    def pick_free_port(start: int, end: int) -> int:
        """Find a free port in the given range."""
        candidates = list(range(start, end + 1))
        random.shuffle(candidates)
        for port in candidates:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("localhost", port)) != 0:
                    return port
        raise RuntimeError(f"No free ports available in range {start}-{end}")

import random
import socket
import time
import logging

import docker
import docker.errors
import httpx

from cooperage.core.config import settings
from cooperage.core.models import ContainerInfo, ServerDef, Session

logger = logging.getLogger(__name__)

_client: docker.DockerClient | None = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


# ── Port helpers ──────────────────────────────────────────────────────────────

def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) != 0


def _pick_free_port() -> int:
    start = settings.container_port_range_start
    end = settings.container_port_range_end
    candidates = list(range(start, end + 1))
    random.shuffle(candidates)
    for port in candidates:
        if _is_port_free(port):
            return port
    raise RuntimeError("No free ports available in configured range")


# ── Image helpers ─────────────────────────────────────────────────────────────

def pull_image(image: str) -> str:
    """Pull a Docker image and return its id. No-op if already present."""
    client = get_client()
    logger.info("Pulling image %s", image)
    img = client.images.pull(image)
    logger.info("Pulled image %s → %s", image, img.id[:19])
    return img.id


def image_exists(image: str) -> bool:
    """Return True if the image is already present locally."""
    client = get_client()
    try:
        client.images.get(image)
        return True
    except docker.errors.ImageNotFound:
        return False


# ── Volume helpers ────────────────────────────────────────────────────────────

def create_volume(volume_name: str) -> None:
    client = get_client()
    client.volumes.create(name=volume_name, labels={"cooperage": "true"})
    logger.info("Created volume %s", volume_name)


def remove_volume(volume_name: str) -> None:
    client = get_client()
    try:
        vol = client.volumes.get(volume_name)
        vol.remove(force=True)
        logger.info("Removed volume %s", volume_name)
    except docker.errors.NotFound:
        pass


# ── Container helpers ─────────────────────────────────────────────────────────

def start_container(server_def: ServerDef, session: Session) -> ContainerInfo:
    client = get_client()
    host_port = _pick_free_port()
    container_name = f"cooperage-{session.id[:8]}-{server_def.name}"

    # Remove any stale container with the same name
    try:
        old = client.containers.get(container_name)
        old.remove(force=True)
        logger.info("Removed stale container %s", container_name)
    except docker.errors.NotFound:
        pass

    env = {
        "COOPERAGE_SESSION_ID": session.id,
        "COOPERAGE_WORKSPACE": settings.workspace_mount,
        **server_def.env,
    }

    container = client.containers.run(
        image=server_def.image,
        name=container_name,
        detach=True,
        remove=False,
        ports={f"{server_def.port}/tcp": host_port},
        volumes={session.volume_name: {"bind": settings.workspace_mount, "mode": "rw"}},
        environment=env,
        labels={"cooperage": "true", "cooperage.session": session.id, "cooperage.server": server_def.name},
    )

    info = ContainerInfo(
        container_id=container.id,
        server_name=server_def.name,
        session_id=session.id,
        host_port=host_port,
    )
    logger.info("Started container %s on port %d", container_name, host_port)
    return info


def stop_container(container_id: str) -> None:
    client = get_client()
    try:
        container = client.containers.get(container_id)
        container.stop(timeout=5)
        container.remove(force=True)
        logger.info("Stopped and removed container %s", container_id[:12])
    except docker.errors.NotFound:
        pass


# ── Readiness check ───────────────────────────────────────────────────────────

def wait_until_ready(info: ContainerInfo, timeout: int | None = None) -> bool:
    """Poll the container's MCP SSE endpoint until it responds or timeout."""
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

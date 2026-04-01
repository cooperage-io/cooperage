import logging

import docker
import docker.errors

from cooperage.core.config import settings
from cooperage.core.models import ContainerInfo, ServerDef, Session
from cooperage.orchestrator.base import Orchestrator

logger = logging.getLogger(__name__)


def _parse_memory_string(mem: str) -> int:
    """Convert '512m', '1g' etc. to bytes for Docker API."""
    mem = mem.strip().lower()
    if mem.endswith("g"):
        return int(float(mem[:-1]) * 1024 * 1024 * 1024)
    if mem.endswith("m"):
        return int(float(mem[:-1]) * 1024 * 1024)
    if mem.endswith("k"):
        return int(float(mem[:-1]) * 1024)
    return int(mem)


class DockerOrchestrator(Orchestrator):
    def __init__(self) -> None:
        self._client: docker.DockerClient | None = None

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def pull_image(self, image: str) -> str:
        image = self.resolve_image(image)
        logger.info("Pulling image %s", image)
        img = self.client.images.pull(image)
        logger.info("Pulled image %s → %s", image, img.id[:19])
        return img.id

    def image_exists(self, image: str) -> bool:
        try:
            self.client.images.get(image)
            return True
        except docker.errors.ImageNotFound:
            return False

    def create_volume(self, volume_name: str) -> None:
        self.client.volumes.create(name=volume_name, labels={"cooperage": "true"})
        logger.info("Created volume %s", volume_name)

    def remove_volume(self, volume_name: str) -> None:
        try:
            vol = self.client.volumes.get(volume_name)
            vol.remove(force=True)
            logger.info("Removed volume %s", volume_name)
        except docker.errors.NotFound:
            pass

    # ── Network isolation ────────────────────────────────────────────────────

    def create_session_network(self, session: Session) -> None:
        if not settings.network_isolation:
            return
        try:
            self.client.networks.create(
                name=session.network_name,
                driver="bridge",
                labels={
                    "cooperage": "true",
                    "cooperage.session": session.id,
                },
                internal=False,  # containers need outbound for image pulls, etc.
            )
            logger.info("Created session network %s", session.network_name)
        except docker.errors.APIError as e:
            if "already exists" in str(e):
                logger.info("Session network %s already exists", session.network_name)
            else:
                raise

    def remove_session_network(self, session: Session) -> None:
        if not settings.network_isolation:
            return
        try:
            network = self.client.networks.get(session.network_name)
            network.remove()
            logger.info("Removed session network %s", session.network_name)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            logger.warning("Failed to remove network %s: %s", session.network_name, e)

    # ── Container lifecycle ──────────────────────────────────────────────────

    def start_container(self, server_def: ServerDef, session: Session) -> ContainerInfo:
        host_port = self.pick_free_port(
            settings.container_port_range_start,
            settings.container_port_range_end,
        )
        container_name = f"cooperage-{session.id[:8]}-{server_def.name}"

        # Remove any stale container with the same name
        try:
            old = self.client.containers.get(container_name)
            old.remove(force=True)
            logger.info("Removed stale container %s", container_name)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            if "already in progress" in str(e):
                logger.info("Container %s removal already in progress, waiting", container_name)
                import time
                time.sleep(2)
            else:
                raise

        # Login to private registry if credentials are provided
        if server_def.registry_credentials:
            creds = server_def.registry_credentials
            self.client.login(
                username=creds.username,
                password=creds.password.get_secret_value(),
                registry=creds.server,
            )
            logger.info("Authenticated to registry %s", creds.server)

        env = {
            "COOPERAGE_SESSION_ID": session.id,
            "COOPERAGE_WORKSPACE": settings.workspace_mount,
            **server_def.env,
        }

        # Resource limits
        cpu, memory = self._resolve_resource_limits(server_def)
        mem_bytes = _parse_memory_string(memory)
        nano_cpus = int(float(cpu) * 1e9)

        # Network config — verify the network still exists before attaching
        network_mode = None
        if settings.network_isolation and session.network_name:
            try:
                self.client.networks.get(session.network_name)
                network_mode = session.network_name
            except docker.errors.NotFound:
                logger.warning("Session network %s missing, recreating", session.network_name)
                self.create_session_network(session)
                network_mode = session.network_name

        container = self.client.containers.run(
            image=self.resolve_image(server_def.image),
            name=container_name,
            detach=True,
            remove=False,
            ports={f"{server_def.port}/tcp": host_port},
            volumes={session.volume_name: {"bind": settings.workspace_mount, "mode": "rw"}},
            environment=env,
            labels={
                "cooperage": "true",
                "cooperage.session": session.id,
                "cooperage.server": server_def.name,
                "cooperage.tenant": session.tenant_id,
            },
            nano_cpus=nano_cpus,
            mem_limit=mem_bytes,
            network=network_mode,
        )

        info = ContainerInfo(
            container_id=container.id,
            server_name=server_def.name,
            session_id=session.id,
            host_port=host_port,
        )
        logger.info(
            "Started container %s on port %d (cpu=%s, mem=%s, net=%s)",
            container_name, host_port, cpu, memory,
            session.network_name if network_mode else "default",
        )
        return info

    def get_container_logs(self, container_id: str, tail: int = 50) -> str:
        try:
            container = self.client.containers.get(container_id)
            return container.logs(tail=tail, timestamps=False).decode("utf-8", errors="replace").strip()
        except docker.errors.NotFound:
            return "(container not found)"
        except Exception as e:
            return f"(could not fetch logs: {e})"

    def stop_container(self, container_id: str) -> None:
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=5)
            container.remove(force=True)
            logger.info("Stopped and removed container %s", container_id[:12])
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            if "already in progress" in str(e) or "is not running" in str(e):
                logger.info("Container %s already stopping/removed", container_id[:12])
            else:
                raise

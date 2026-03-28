import logging

import docker
import docker.errors

from cooperage.core.config import settings
from cooperage.core.models import ContainerInfo, ServerDef, Session
from cooperage.orchestrator.base import Orchestrator

logger = logging.getLogger(__name__)


class DockerOrchestrator(Orchestrator):
    def __init__(self) -> None:
        self._client: docker.DockerClient | None = None

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def pull_image(self, image: str) -> str:
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

        env = {
            "COOPERAGE_SESSION_ID": session.id,
            "COOPERAGE_WORKSPACE": settings.workspace_mount,
            **server_def.env,
        }

        container = self.client.containers.run(
            image=server_def.image,
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
            },
        )

        info = ContainerInfo(
            container_id=container.id,
            server_name=server_def.name,
            session_id=session.id,
            host_port=host_port,
        )
        logger.info("Started container %s on port %d", container_name, host_port)
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

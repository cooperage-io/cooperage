"""
Cooperage Kubernetes Orchestrator

Drop-in replacement for DockerOrchestrator. Uses the Kubernetes Python client to run
MCP server containers as Pods with NodePort Services.

Works out of the box with Docker Desktop Kubernetes (single-node). For
multi-node clusters, replace the hostPath volume with a PVC backed by a
ReadWriteMany StorageClass (e.g. EFS, NFS).
"""

import time
import logging

from cooperage.core.config import settings
from cooperage.core.models import ContainerInfo, ServerDef, Session
from cooperage.orchestrator.base import Orchestrator

logger = logging.getLogger(__name__)


class KubernetesOrchestrator(Orchestrator):
    def __init__(self) -> None:
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from kubernetes import client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            self._client = client
        return self._client

    @property
    def _ns(self) -> str:
        return settings.k8s_namespace

    @property
    def _core(self):
        return self.client.CoreV1Api()

    def pull_image(self, image: str) -> str:
        """Pre-pull an image onto the node by running a short-lived Pod."""
        # Sanitize image name into a valid DNS label
        safe = image.lower()
        for ch in "/:.@":
            safe = safe.replace(ch, "-")
        pod_name = f"cooperage-pull-{safe}"[:63]

        # Remove any stale pull pod
        self._delete_pod(pod_name)

        pod = self.client.V1Pod(
            metadata=self.client.V1ObjectMeta(
                name=pod_name,
                namespace=self._ns,
                labels={"cooperage": "true"},
            ),
            spec=self.client.V1PodSpec(
                restart_policy="Never",
                containers=[self.client.V1Container(
                    name="pull",
                    image=image,
                    command=["true"],
                )],
            ),
        )

        self._core.create_namespaced_pod(namespace=self._ns, body=pod)
        logger.info("K8s: pre-pulling image %s via pod %s", image, pod_name)

        deadline = time.monotonic() + max(settings.container_startup_timeout * 4, 120)
        while time.monotonic() < deadline:
            p = self._core.read_namespaced_pod(name=pod_name, namespace=self._ns)
            if p.status.phase in ("Succeeded", "Failed"):
                break
            time.sleep(2)

        self._delete_pod(pod_name)
        logger.info("K8s: image %s is now cached on node", image)
        return image

    def image_exists(self, image: str) -> bool:
        return True  # Kubernetes handles image availability

    def create_volume(self, volume_name: str) -> None:
        # No-op — hostPath directory is created on first Pod write
        logger.info("K8s: create_volume is a no-op (hostPath) for %s", volume_name)

    def remove_volume(self, volume_name: str) -> None:
        """Spawn a short-lived cleanup Pod to delete the hostPath directory."""
        host_path = f"{settings.k8s_host_path_prefix}/{volume_name}"
        cleanup_name = f"cooperage-cleanup-{volume_name[:16]}"

        pod = self.client.V1Pod(
            metadata=self.client.V1ObjectMeta(
                name=cleanup_name,
                namespace=self._ns,
                labels={"cooperage": "true"},
            ),
            spec=self.client.V1PodSpec(
                restart_policy="Never",
                containers=[self.client.V1Container(
                    name="cleanup",
                    image="busybox:latest",
                    command=["rm", "-rf", "/cleanup-target"],
                    volume_mounts=[self.client.V1VolumeMount(
                        name="workspace",
                        mount_path="/cleanup-target",
                    )],
                )],
                volumes=[self.client.V1Volume(
                    name="workspace",
                    host_path=self.client.V1HostPathVolumeSource(
                        path=host_path,
                        type="DirectoryOrCreate",
                    ),
                )],
            ),
        )

        try:
            self._core.create_namespaced_pod(namespace=self._ns, body=pod)
            logger.info("K8s: launched cleanup pod %s for volume %s", cleanup_name, volume_name)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                p = self._core.read_namespaced_pod(name=cleanup_name, namespace=self._ns)
                if p.status.phase in ("Succeeded", "Failed"):
                    break
                time.sleep(1)
        except Exception as e:
            logger.warning("K8s: cleanup pod failed for %s: %s", volume_name, e)
        finally:
            self._delete_pod(cleanup_name)

    def start_container(self, server_def: ServerDef, session: Session) -> ContainerInfo:
        host_port = self.pick_free_port(
            settings.k8s_node_port_range_start,
            settings.k8s_node_port_range_end,
        )
        pod_name = f"cooperage-{session.id[:8]}-{server_def.name}"
        host_path = f"{settings.k8s_host_path_prefix}/{session.volume_name}"

        # Remove any stale Pod/Service with same name
        self._delete_pod(pod_name)
        self._delete_service(pod_name)

        env_vars = [
            self.client.V1EnvVar(name="COOPERAGE_SESSION_ID", value=session.id),
            self.client.V1EnvVar(name="COOPERAGE_WORKSPACE", value=settings.workspace_mount),
            *[self.client.V1EnvVar(name=k, value=v) for k, v in server_def.env.items()],
        ]

        pod = self.client.V1Pod(
            metadata=self.client.V1ObjectMeta(
                name=pod_name,
                namespace=self._ns,
                labels={
                    "cooperage": "true",
                    "cooperage.session": session.id,
                    "cooperage.server": server_def.name,
                },
            ),
            spec=self.client.V1PodSpec(
                restart_policy="Never",
                containers=[self.client.V1Container(
                    name=server_def.name,
                    image=server_def.image,
                    ports=[self.client.V1ContainerPort(container_port=server_def.port)],
                    env=env_vars,
                    volume_mounts=[self.client.V1VolumeMount(
                        name="workspace",
                        mount_path=settings.workspace_mount,
                    )],
                )],
                volumes=[self.client.V1Volume(
                    name="workspace",
                    host_path=self.client.V1HostPathVolumeSource(
                        path=host_path,
                        type="DirectoryOrCreate",
                    ),
                )],
            ),
        )

        service = self.client.V1Service(
            metadata=self.client.V1ObjectMeta(
                name=pod_name,
                namespace=self._ns,
                labels={"cooperage": "true"},
            ),
            spec=self.client.V1ServiceSpec(
                type="NodePort",
                selector={
                    "cooperage.session": session.id,
                    "cooperage.server": server_def.name,
                },
                ports=[self.client.V1ServicePort(
                    port=server_def.port,
                    target_port=server_def.port,
                    node_port=host_port,
                )],
            ),
        )

        self._core.create_namespaced_pod(namespace=self._ns, body=pod)
        self._core.create_namespaced_service(namespace=self._ns, body=service)

        info = ContainerInfo(
            container_id=pod_name,
            server_name=server_def.name,
            session_id=session.id,
            host_port=host_port,
        )
        logger.info("K8s: started pod %s on NodePort %d", pod_name, host_port)
        return info

    def get_container_logs(self, container_id: str, tail: int = 50) -> str:
        try:
            return self._core.read_namespaced_pod_log(
                name=container_id,
                namespace=self._ns,
                tail_lines=tail,
            )
        except Exception as e:
            return f"(could not fetch K8s pod logs: {e})"

    def stop_container(self, container_id: str) -> None:
        self._delete_pod(container_id)
        self._delete_service(container_id)
        logger.info("K8s: deleted pod+service %s", container_id)

    def _delete_pod(self, name: str) -> None:
        try:
            self._core.delete_namespaced_pod(name=name, namespace=self._ns)
        except Exception:
            pass

    def _delete_service(self, name: str) -> None:
        try:
            self._core.delete_namespaced_service(name=name, namespace=self._ns)
        except Exception:
            pass

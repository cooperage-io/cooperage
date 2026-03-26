"""
Cooperage Kubernetes Orchestrator

Drop-in replacement for docker.py. Uses the Kubernetes Python client to run
MCP server containers as Pods with NodePort Services.

Works out of the box with Docker Desktop Kubernetes (single-node). For
multi-node clusters, replace the hostPath volume with a PVC backed by a
ReadWriteMany StorageClass (e.g. EFS, NFS).
"""

import random
import socket
import time
import logging

from cooperage.core.config import settings
from cooperage.core.models import ContainerInfo, ServerDef, Session

logger = logging.getLogger(__name__)

_k8s_client = None


def _get_client():
    global _k8s_client
    if _k8s_client is None:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        _k8s_client = client
    return _k8s_client


# ── Port helpers ──────────────────────────────────────────────────────────────

def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) != 0


def _pick_free_port() -> int:
    start = settings.k8s_node_port_range_start
    end = settings.k8s_node_port_range_end
    candidates = list(range(start, end + 1))
    random.shuffle(candidates)
    for port in candidates:
        if _is_port_free(port):
            return port
    raise RuntimeError("No free NodePorts available in configured range")


# ── Image helpers ─────────────────────────────────────────────────────────────

def pull_image(image: str) -> str:
    """No-op — Kubernetes pulls images lazily on Pod creation."""
    logger.info("K8s: pull_image is a no-op (K8s pulls on Pod creation) for %s", image)
    return image


def image_exists(image: str) -> bool:
    """Always True — Kubernetes handles image availability."""
    return True


# ── Volume helpers ────────────────────────────────────────────────────────────

def create_volume(volume_name: str) -> None:
    """No-op — hostPath directory is created on first Pod write."""
    logger.info("K8s: create_volume is a no-op (hostPath dir created by Pod) for %s", volume_name)


def remove_volume(volume_name: str) -> None:
    """Spawn a short-lived cleanup Pod to delete the hostPath directory."""
    client = _get_client()
    ns = settings.k8s_namespace
    host_path = f"{settings.k8s_host_path_prefix}/{volume_name}"
    cleanup_name = f"cooperage-cleanup-{volume_name[:16]}"

    core = client.CoreV1Api()

    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=cleanup_name,
            namespace=ns,
            labels={"cooperage": "true"},
        ),
        spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[client.V1Container(
                name="cleanup",
                image="busybox:latest",
                command=["sh", "-c", f"rm -rf {host_path}"],
                volume_mounts=[client.V1VolumeMount(
                    name="workspace",
                    mount_path=host_path,
                )],
            )],
            volumes=[client.V1Volume(
                name="workspace",
                host_path=client.V1HostPathVolumeSource(
                    path=host_path,
                    type="DirectoryOrCreate",
                ),
            )],
        ),
    )

    try:
        core.create_namespaced_pod(namespace=ns, body=pod)
        logger.info("K8s: launched cleanup pod %s for volume %s", cleanup_name, volume_name)
        # Wait briefly for the cleanup pod, then delete it
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            p = core.read_namespaced_pod(name=cleanup_name, namespace=ns)
            if p.status.phase in ("Succeeded", "Failed"):
                break
            time.sleep(1)
    except Exception as e:
        logger.warning("K8s: cleanup pod failed for %s: %s", volume_name, e)
    finally:
        try:
            core.delete_namespaced_pod(name=cleanup_name, namespace=ns)
        except Exception:
            pass


# ── Container helpers ─────────────────────────────────────────────────────────

def start_container(server_def: ServerDef, session: Session) -> ContainerInfo:
    client = _get_client()
    ns = settings.k8s_namespace
    host_port = _pick_free_port()
    pod_name = f"cooperage-{session.id[:8]}-{server_def.name}"
    host_path = f"{settings.k8s_host_path_prefix}/{session.volume_name}"

    core = client.CoreV1Api()

    # Remove any stale Pod/Service with same name
    for delete in (_delete_pod, _delete_service):
        try:
            delete(client, ns, pod_name)
        except Exception:
            pass

    env_vars = [
        client.V1EnvVar(name="COOPERAGE_SESSION_ID", value=session.id),
        client.V1EnvVar(name="COOPERAGE_WORKSPACE", value=settings.workspace_mount),
        *[client.V1EnvVar(name=k, value=v) for k, v in server_def.env.items()],
    ]

    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=pod_name,
            namespace=ns,
            labels={
                "cooperage": "true",
                "cooperage.session": session.id,
                "cooperage.server": server_def.name,
            },
        ),
        spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[client.V1Container(
                name=server_def.name,
                image=server_def.image,
                ports=[client.V1ContainerPort(container_port=server_def.port)],
                env=env_vars,
                volume_mounts=[client.V1VolumeMount(
                    name="workspace",
                    mount_path=settings.workspace_mount,
                )],
            )],
            volumes=[client.V1Volume(
                name="workspace",
                host_path=client.V1HostPathVolumeSource(
                    path=host_path,
                    type="DirectoryOrCreate",
                ),
            )],
        ),
    )

    service = client.V1Service(
        metadata=client.V1ObjectMeta(
            name=pod_name,
            namespace=ns,
            labels={"cooperage": "true"},
        ),
        spec=client.V1ServiceSpec(
            type="NodePort",
            selector={
                "cooperage.session": session.id,
                "cooperage.server": server_def.name,
            },
            ports=[client.V1ServicePort(
                port=server_def.port,
                target_port=server_def.port,
                node_port=host_port,
            )],
        ),
    )

    core.create_namespaced_pod(namespace=ns, body=pod)
    client.CoreV1Api().create_namespaced_service(namespace=ns, body=service)

    info = ContainerInfo(
        container_id=pod_name,
        server_name=server_def.name,
        session_id=session.id,
        host_port=host_port,
    )
    logger.info("K8s: started pod %s on NodePort %d", pod_name, host_port)
    return info


def stop_container(container_id: str) -> None:
    """Delete the Pod and Service identified by container_id (the pod name)."""
    client = _get_client()
    ns = settings.k8s_namespace
    _delete_pod(client, ns, container_id)
    _delete_service(client, ns, container_id)
    logger.info("K8s: deleted pod+service %s", container_id)


def _delete_pod(client, ns: str, name: str) -> None:
    try:
        client.CoreV1Api().delete_namespaced_pod(name=name, namespace=ns)
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise


def _delete_service(client, ns: str, name: str) -> None:
    try:
        client.CoreV1Api().delete_namespaced_service(name=name, namespace=ns)
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise


# ── Readiness check ───────────────────────────────────────────────────────────

def wait_until_ready(info: ContainerInfo, timeout: int | None = None) -> bool:
    """Poll the NodePort endpoint until the MCP server responds or timeout."""
    import httpx
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

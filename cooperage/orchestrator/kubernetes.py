"""
Cooperage Kubernetes Orchestrator

Drop-in replacement for DockerOrchestrator. Uses the Kubernetes Python client to run
MCP server containers as Pods with NodePort Services.

Works out of the box with Docker Desktop Kubernetes (single-node). For
multi-node clusters, replace the hostPath volume with a PVC backed by a
ReadWriteMany StorageClass (e.g. EFS, NFS).
"""

import base64
import json
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

    @property
    def _networking(self):
        return self.client.NetworkingV1Api()

    def pull_image(self, image: str) -> str:
        """Pre-pull an image onto the node by running a short-lived Pod."""
        safe = image.lower()
        for ch in "/:.@":
            safe = safe.replace(ch, "-")
        pod_name = f"cooperage-pull-{safe}"[:63]

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
        return True

    def create_volume(self, volume_name: str) -> None:
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

    # ── Network isolation (NetworkPolicy) ────────────────────────────────────

    def create_session_network(self, session: Session) -> None:
        if not settings.network_isolation:
            return

        policy_name = f"cooperage-isolate-{session.id[:12]}"
        policy = self.client.V1NetworkPolicy(
            metadata=self.client.V1ObjectMeta(
                name=policy_name,
                namespace=self._ns,
                labels={
                    "cooperage": "true",
                    "cooperage.session": session.id,
                },
            ),
            spec=self.client.V1NetworkPolicySpec(
                # Applies to pods in this session
                pod_selector=self.client.V1LabelSelector(
                    match_labels={"cooperage.session": session.id},
                ),
                policy_types=["Ingress"],
                ingress=[
                    self.client.V1NetworkPolicyIngressRule(
                        from_=[
                            # Allow traffic from pods in the same session
                            self.client.V1NetworkPolicyPeer(
                                pod_selector=self.client.V1LabelSelector(
                                    match_labels={"cooperage.session": session.id},
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        )

        try:
            self._networking.create_namespaced_network_policy(
                namespace=self._ns, body=policy,
            )
            logger.info("K8s: created NetworkPolicy %s for session %s", policy_name, session.id[:8])
        except Exception as e:
            logger.warning("K8s: failed to create NetworkPolicy for session %s: %s", session.id[:8], e)

    def remove_session_network(self, session: Session) -> None:
        if not settings.network_isolation:
            return

        policy_name = f"cooperage-isolate-{session.id[:12]}"
        try:
            self._networking.delete_namespaced_network_policy(
                name=policy_name, namespace=self._ns,
            )
            logger.info("K8s: removed NetworkPolicy %s", policy_name)
        except Exception:
            pass

    # ── imagePullSecrets ─────────────────────────────────────────────────────

    def _ensure_pull_secret(self, server_def: ServerDef, session: Session) -> str | None:
        """Create a K8s Secret for private registry auth. Returns secret name or None."""
        creds = server_def.registry_credentials
        if creds is None:
            return None

        secret_name = f"cooperage-regcred-{session.id[:8]}-{server_def.name}"[:63]

        # Build .dockerconfigjson
        auth_str = base64.b64encode(f"{creds.username}:{creds.password}".encode()).decode()
        docker_config = {
            "auths": {
                creds.server: {
                    "username": creds.username,
                    "password": creds.password,
                    "auth": auth_str,
                }
            }
        }

        secret = self.client.V1Secret(
            metadata=self.client.V1ObjectMeta(
                name=secret_name,
                namespace=self._ns,
                labels={"cooperage": "true", "cooperage.session": session.id},
            ),
            type="kubernetes.io/dockerconfigjson",
            data={
                ".dockerconfigjson": base64.b64encode(
                    json.dumps(docker_config).encode()
                ).decode(),
            },
        )

        try:
            self._core.create_namespaced_secret(namespace=self._ns, body=secret)
        except Exception:
            # Secret may already exist (idempotent)
            try:
                self._core.replace_namespaced_secret(
                    name=secret_name, namespace=self._ns, body=secret,
                )
            except Exception as e:
                logger.warning("K8s: failed to create/update pull secret %s: %s", secret_name, e)
                return None

        logger.info("K8s: created pull secret %s for %s", secret_name, creds.server)
        return secret_name

    # ── Container lifecycle ──────────────────────────────────────────────────

    def start_container(self, server_def: ServerDef, session: Session) -> ContainerInfo:
        host_port = self.pick_free_port(
            settings.k8s_node_port_range_start,
            settings.k8s_node_port_range_end,
        )
        pod_name = f"cooperage-{session.id[:8]}-{server_def.name}"
        host_path = f"{settings.k8s_host_path_prefix}/{session.volume_name}"

        self._delete_pod(pod_name)
        self._delete_service(pod_name)

        # Resource limits
        cpu, memory = self._resolve_resource_limits(server_def)
        resource_requirements = self.client.V1ResourceRequirements(
            limits={"cpu": cpu, "memory": memory},
            requests={"cpu": cpu, "memory": memory},
        )

        env_vars = [
            self.client.V1EnvVar(name="COOPERAGE_SESSION_ID", value=session.id),
            self.client.V1EnvVar(name="COOPERAGE_WORKSPACE", value=settings.workspace_mount),
            *[self.client.V1EnvVar(name=k, value=v) for k, v in server_def.env.items()],
        ]

        # imagePullSecrets
        pull_secret_name = self._ensure_pull_secret(server_def, session)
        image_pull_secrets = (
            [self.client.V1LocalObjectReference(name=pull_secret_name)]
            if pull_secret_name else None
        )

        pod = self.client.V1Pod(
            metadata=self.client.V1ObjectMeta(
                name=pod_name,
                namespace=self._ns,
                labels={
                    "cooperage": "true",
                    "cooperage.session": session.id,
                    "cooperage.server": server_def.name,
                    "cooperage.tenant": session.tenant_id,
                },
            ),
            spec=self.client.V1PodSpec(
                restart_policy="Never",
                image_pull_secrets=image_pull_secrets,
                containers=[self.client.V1Container(
                    name=server_def.name,
                    image=server_def.image,
                    ports=[self.client.V1ContainerPort(container_port=server_def.port)],
                    env=env_vars,
                    resources=resource_requirements,
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
        logger.info(
            "K8s: started pod %s on NodePort %d (cpu=%s, mem=%s)",
            pod_name, host_port, cpu, memory,
        )
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

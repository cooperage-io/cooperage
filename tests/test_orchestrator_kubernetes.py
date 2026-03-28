"""
Kubernetes orchestrator tests — all kubernetes client calls are mocked.
No real cluster required.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from cooperage.core.models import ContainerInfo, ServerDef, Session
from cooperage.orchestrator.kubernetes import KubernetesOrchestrator


def _make_session(**kwargs) -> Session:
    defaults = dict(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    defaults.update(kwargs)
    return Session(**defaults)


def _make_mock_k8s_client():
    """Return a mock kubernetes client module with CoreV1Api."""
    client = MagicMock()
    core_api = MagicMock()
    client.CoreV1Api.return_value = core_api
    client.exceptions.ApiException = type("ApiException", (Exception,), {"status": 404})
    return client, core_api


def _make_orch(mock_client) -> KubernetesOrchestrator:
    orch = KubernetesOrchestrator()
    orch._client = mock_client
    return orch


# ── pull_image / image_exists / create_volume ─────────────────────────────────

@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2])
def test_pull_image_spawns_and_deletes_pull_pod(mock_mono, _mock_sleep):
    client, core_api = _make_mock_k8s_client()

    pod_status = MagicMock()
    pod_status.status.phase = "Succeeded"
    core_api.read_namespaced_pod.return_value = pod_status

    orch = _make_orch(client)
    result = orch.pull_image("cooperage-image-analyzer:latest")

    assert result == "cooperage-image-analyzer:latest"
    core_api.create_namespaced_pod.assert_called_once()
    core_api.delete_namespaced_pod.assert_called()

    meta_calls = [str(c) for c in client.V1ObjectMeta.call_args_list]
    assert any("cooperage-pull" in c for c in meta_calls)


@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2])
def test_pull_image_still_cleans_up_on_failed_pod(mock_mono, _mock_sleep):
    client, core_api = _make_mock_k8s_client()

    pod_status = MagicMock()
    pod_status.status.phase = "Failed"
    core_api.read_namespaced_pod.return_value = pod_status

    orch = _make_orch(client)
    orch.pull_image("cooperage-image-analyzer:latest")  # should not raise

    core_api.delete_namespaced_pod.assert_called()


def test_image_exists_always_true():
    orch = KubernetesOrchestrator()
    assert orch.image_exists("anything:latest") is True


def test_create_volume_is_noop():
    orch = KubernetesOrchestrator()
    orch.create_volume("cooperage-session-abc")  # should not raise


# ── remove_volume ─────────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2])
def test_remove_volume_creates_and_deletes_cleanup_pod(mock_mono, _mock_sleep):
    client, core_api = _make_mock_k8s_client()

    pod_status = MagicMock()
    pod_status.status.phase = "Succeeded"
    core_api.read_namespaced_pod.return_value = pod_status

    orch = _make_orch(client)
    orch.remove_volume("cooperage-session-abc")

    core_api.create_namespaced_pod.assert_called_once()
    # delete is called in _delete_pod (in finally) + potentially in _delete_pod before create
    assert core_api.delete_namespaced_pod.call_count >= 1

    meta_calls = [str(c) for c in client.V1ObjectMeta.call_args_list]
    assert any("cooperage-cleanup" in c for c in meta_calls)


@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2])
def test_remove_volume_still_deletes_pod_on_failure(mock_mono, _mock_sleep):
    client, core_api = _make_mock_k8s_client()
    core_api.create_namespaced_pod.side_effect = Exception("k8s error")

    orch = _make_orch(client)
    orch.remove_volume("cooperage-session-abc")  # should not raise

    core_api.delete_namespaced_pod.assert_called()


# ── start_container ───────────────────────────────────────────────────────────

@patch.object(KubernetesOrchestrator, "pick_free_port", return_value=30001)
def test_start_container_returns_container_info(_mock_port):
    client, core_api = _make_mock_k8s_client()

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest", port=8000)

    orch = _make_orch(client)
    info = orch.start_container(server_def, session)

    assert info.server_name == "sim"
    assert info.session_id == session.id
    assert info.host_port == 30001
    assert info.mcp_url == "http://localhost:30001"
    assert f"cooperage-{session.id[:8]}-sim" in info.container_id


@patch.object(KubernetesOrchestrator, "pick_free_port", return_value=30002)
def test_start_container_creates_pod_and_service(_mock_port):
    client, core_api = _make_mock_k8s_client()

    session = _make_session()
    server_def = ServerDef(name="image-analyzer", image="image-analyzer:latest", port=8000)

    orch = _make_orch(client)
    orch.start_container(server_def, session)

    core_api.create_namespaced_pod.assert_called_once()
    core_api.create_namespaced_service.assert_called_once()


@patch.object(KubernetesOrchestrator, "pick_free_port", return_value=30003)
def test_start_container_passes_env_vars(_mock_port):
    client, core_api = _make_mock_k8s_client()

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest", env={"MY_VAR": "hello"})

    orch = _make_orch(client)
    orch.start_container(server_def, session)

    env_calls = {call.kwargs["name"]: call.kwargs["value"] for call in client.V1EnvVar.call_args_list}
    assert env_calls["COOPERAGE_SESSION_ID"] == session.id
    assert env_calls["MY_VAR"] == "hello"


@patch.object(KubernetesOrchestrator, "pick_free_port", return_value=30004)
def test_start_container_mounts_workspace_hostpath(_mock_port):
    client, core_api = _make_mock_k8s_client()

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest")

    orch = _make_orch(client)
    from cooperage.core.config import settings
    orch.start_container(server_def, session)

    host_path_call = client.V1HostPathVolumeSource.call_args
    path_arg = host_path_call.kwargs["path"]
    assert session.volume_name in path_arg
    assert settings.k8s_host_path_prefix in path_arg


# ── stop_container ────────────────────────────────────────────────────────────

def test_stop_container_deletes_pod_and_service():
    client, core_api = _make_mock_k8s_client()

    orch = _make_orch(client)
    orch.stop_container("cooperage-abc12345-sim")

    core_api.delete_namespaced_pod.assert_called_once()
    core_api.delete_namespaced_service.assert_called_once()


def test_stop_container_ignores_not_found():
    client, core_api = _make_mock_k8s_client()

    not_found = client.exceptions.ApiException()
    not_found.status = 404
    core_api.delete_namespaced_pod.side_effect = not_found
    core_api.delete_namespaced_service.side_effect = not_found

    orch = _make_orch(client)
    orch.stop_container("ghost")  # should not raise


# ── get_container_logs ───────────────────────────────────────────────────────

def test_get_container_logs():
    client, core_api = _make_mock_k8s_client()
    core_api.read_namespaced_pod_log.return_value = "some log output"

    orch = _make_orch(client)
    result = orch.get_container_logs("cooperage-abc12345-sim")

    assert result == "some log output"
    core_api.read_namespaced_pod_log.assert_called_once()


def test_get_container_logs_handles_error():
    client, core_api = _make_mock_k8s_client()
    core_api.read_namespaced_pod_log.side_effect = Exception("k8s error")

    orch = _make_orch(client)
    result = orch.get_container_logs("ghost")

    assert "could not fetch" in result


# ── wait_until_ready (inherited from base) ───────────────────────────────────

@patch("cooperage.orchestrator.base.time.sleep")
@patch("cooperage.orchestrator.base.httpx.get")
def test_wait_until_ready_succeeds(mock_get, _mock_sleep):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_get.return_value = mock_response

    info = ContainerInfo(container_id="p1", server_name="sim", session_id="s1", host_port=30001)

    orch = KubernetesOrchestrator()
    assert orch.wait_until_ready(info, timeout=5) is True


@patch("cooperage.orchestrator.base.time.monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6])
@patch("cooperage.orchestrator.base.time.sleep")
@patch("cooperage.orchestrator.base.httpx.get")
def test_wait_until_ready_times_out(mock_get, _mock_sleep, _mock_mono):
    import httpx
    mock_get.side_effect = httpx.RequestError("refused")

    info = ContainerInfo(container_id="p1", server_name="sim", session_id="s1", host_port=30001)

    orch = KubernetesOrchestrator()
    assert orch.wait_until_ready(info, timeout=5) is False


# ── get_orchestrator factory ──────────────────────────────────────────────────

def test_get_orchestrator_returns_docker_by_default():
    import cooperage.orchestrator as orch_pkg
    from cooperage.orchestrator.docker import DockerOrchestrator
    # Reset singleton
    orch_pkg._instance = None
    orch = orch_pkg.get_orchestrator()
    assert isinstance(orch, DockerOrchestrator)
    orch_pkg._instance = None  # cleanup


@patch("cooperage.core.config.settings.orchestrator", "kubernetes")
def test_get_orchestrator_returns_kubernetes_when_configured():
    import cooperage.orchestrator as orch_pkg
    orch_pkg._instance = None
    orch = orch_pkg.get_orchestrator()
    assert isinstance(orch, KubernetesOrchestrator)
    orch_pkg._instance = None  # cleanup

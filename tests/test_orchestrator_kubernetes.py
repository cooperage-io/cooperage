"""
Kubernetes orchestrator tests — all kubernetes client calls are mocked.
No real cluster required.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call
import pytest

from cooperage.core.models import ContainerInfo, ServerDef, Session


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


# ── pull_image / image_exists / create_volume ─────────────────────────────────

@patch("cooperage.orchestrator.kubernetes._get_client")
@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2])
def test_pull_image_spawns_and_deletes_pull_pod(mock_mono, _mock_sleep, mock_get_client):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client

    pod_status = MagicMock()
    pod_status.status.phase = "Succeeded"
    core_api.read_namespaced_pod.return_value = pod_status

    from cooperage.orchestrator.kubernetes import pull_image
    result = pull_image("cooperage-analysis:latest")

    assert result == "cooperage-analysis:latest"
    core_api.create_namespaced_pod.assert_called_once()
    core_api.delete_namespaced_pod.assert_called()

    # Pod name should be derived from the image name
    meta_calls = [str(c) for c in client.V1ObjectMeta.call_args_list]
    assert any("cooperage-pull" in c for c in meta_calls)


@patch("cooperage.orchestrator.kubernetes._get_client")
@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2])
def test_pull_image_still_cleans_up_on_failed_pod(mock_mono, _mock_sleep, mock_get_client):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client

    pod_status = MagicMock()
    pod_status.status.phase = "Failed"
    core_api.read_namespaced_pod.return_value = pod_status

    from cooperage.orchestrator.kubernetes import pull_image
    pull_image("cooperage-analysis:latest")  # should not raise

    core_api.delete_namespaced_pod.assert_called()


def test_image_exists_always_true():
    from cooperage.orchestrator.kubernetes import image_exists
    assert image_exists("anything:latest") is True


def test_create_volume_is_noop():
    from cooperage.orchestrator.kubernetes import create_volume
    create_volume("cooperage-session-abc")  # should not raise


# ── remove_volume ─────────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.kubernetes._get_client")
@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2])
def test_remove_volume_creates_and_deletes_cleanup_pod(mock_mono, _mock_sleep, mock_get_client):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client

    pod_status = MagicMock()
    pod_status.status.phase = "Succeeded"
    core_api.read_namespaced_pod.return_value = pod_status

    from cooperage.orchestrator.kubernetes import remove_volume
    remove_volume("cooperage-session-abc")

    core_api.create_namespaced_pod.assert_called_once()
    core_api.delete_namespaced_pod.assert_called_once()

    # V1ObjectMeta was called with name="cooperage-cleanup-..."
    meta_calls = [str(c) for c in client.V1ObjectMeta.call_args_list]
    assert any("cooperage-cleanup" in c for c in meta_calls)


@patch("cooperage.orchestrator.kubernetes._get_client")
@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2])
def test_remove_volume_still_deletes_pod_on_failure(mock_mono, _mock_sleep, mock_get_client):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client
    core_api.create_namespaced_pod.side_effect = Exception("k8s error")

    from cooperage.orchestrator.kubernetes import remove_volume
    remove_volume("cooperage-session-abc")  # should not raise

    core_api.delete_namespaced_pod.assert_called_once()


# ── start_container ───────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.kubernetes._pick_free_port", return_value=30001)
@patch("cooperage.orchestrator.kubernetes._get_client")
def test_start_container_returns_container_info(mock_get_client, _mock_port):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest", port=8000)

    from cooperage.orchestrator.kubernetes import start_container
    info = start_container(server_def, session)

    assert info.server_name == "sim"
    assert info.session_id == session.id
    assert info.host_port == 30001
    assert info.mcp_url == "http://localhost:30001"
    assert f"cooperage-{session.id[:8]}-sim" in info.container_id


@patch("cooperage.orchestrator.kubernetes._pick_free_port", return_value=30002)
@patch("cooperage.orchestrator.kubernetes._get_client")
def test_start_container_creates_pod_and_service(mock_get_client, _mock_port):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client

    session = _make_session()
    server_def = ServerDef(name="analysis", image="analysis:latest", port=8000)

    from cooperage.orchestrator.kubernetes import start_container
    start_container(server_def, session)

    core_api.create_namespaced_pod.assert_called_once()
    core_api.create_namespaced_service.assert_called_once()


@patch("cooperage.orchestrator.kubernetes._pick_free_port", return_value=30003)
@patch("cooperage.orchestrator.kubernetes._get_client")
def test_start_container_passes_env_vars(mock_get_client, _mock_port):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest", env={"MY_VAR": "hello"})

    from cooperage.orchestrator.kubernetes import start_container
    start_container(server_def, session)

    # V1EnvVar was called once per env var — check the call args
    env_calls = {call.kwargs["name"]: call.kwargs["value"] for call in client.V1EnvVar.call_args_list}
    assert env_calls["COOPERAGE_SESSION_ID"] == session.id
    assert env_calls["MY_VAR"] == "hello"


@patch("cooperage.orchestrator.kubernetes._pick_free_port", return_value=30004)
@patch("cooperage.orchestrator.kubernetes._get_client")
def test_start_container_mounts_workspace_hostpath(mock_get_client, _mock_port):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest")

    from cooperage.orchestrator.kubernetes import start_container
    from cooperage.core.config import settings
    start_container(server_def, session)

    # V1HostPathVolumeSource was called with the session volume path
    host_path_call = client.V1HostPathVolumeSource.call_args
    path_arg = host_path_call.kwargs["path"]
    assert session.volume_name in path_arg
    assert settings.k8s_host_path_prefix in path_arg


# ── stop_container ────────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.kubernetes._get_client")
def test_stop_container_deletes_pod_and_service(mock_get_client):
    client, core_api = _make_mock_k8s_client()
    mock_get_client.return_value = client

    from cooperage.orchestrator.kubernetes import stop_container
    stop_container("cooperage-abc12345-sim")

    core_api.delete_namespaced_pod.assert_called_once()
    core_api.delete_namespaced_service.assert_called_once()


@patch("cooperage.orchestrator.kubernetes._get_client")
def test_stop_container_ignores_not_found(mock_get_client):
    client, core_api = _make_mock_k8s_client()

    not_found = client.exceptions.ApiException()
    not_found.status = 404
    core_api.delete_namespaced_pod.side_effect = not_found
    core_api.delete_namespaced_service.side_effect = not_found
    mock_get_client.return_value = client

    from cooperage.orchestrator.kubernetes import stop_container
    stop_container("ghost")  # should not raise


# ── wait_until_ready ──────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("httpx.get")
def test_wait_until_ready_succeeds(mock_get, _mock_sleep):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_get.return_value = mock_response

    info = ContainerInfo(container_id="p1", server_name="sim", session_id="s1", host_port=30001)

    from cooperage.orchestrator.kubernetes import wait_until_ready
    assert wait_until_ready(info, timeout=5) is True


@patch("cooperage.orchestrator.kubernetes.time.monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6])
@patch("cooperage.orchestrator.kubernetes.time.sleep")
@patch("httpx.get")
def test_wait_until_ready_times_out(mock_get, _mock_sleep, _mock_mono):
    import httpx
    mock_get.side_effect = httpx.RequestError("refused")

    info = ContainerInfo(container_id="p1", server_name="sim", session_id="s1", host_port=30001)

    from cooperage.orchestrator.kubernetes import wait_until_ready
    assert wait_until_ready(info, timeout=5) is False


# ── get_orchestrator factory ──────────────────────────────────────────────────

def test_get_orchestrator_returns_docker_by_default():
    from cooperage.orchestrator import get_orchestrator
    from cooperage.orchestrator import docker
    assert get_orchestrator() is docker


@patch("cooperage.core.config.settings.orchestrator", "kubernetes")
def test_get_orchestrator_returns_kubernetes_when_configured():
    # Reset the cached module-level orch in kubernetes module
    import cooperage.orchestrator as orch_pkg
    from cooperage.orchestrator import kubernetes
    result = orch_pkg.get_orchestrator()
    assert result is kubernetes

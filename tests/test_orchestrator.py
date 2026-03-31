"""
Orchestrator tests — all Docker SDK calls are mocked.
We never touch a real Docker daemon here.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from cooperage.core.models import ContainerInfo, ServerDef, Session
from cooperage.orchestrator.docker import DockerOrchestrator


def _make_session(**kwargs) -> Session:
    defaults = dict(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    defaults.update(kwargs)
    return Session(**defaults)


def _make_orch(mock_client: MagicMock) -> DockerOrchestrator:
    orch = DockerOrchestrator()
    orch._client = mock_client
    return orch


# ── pull_image ────────────────────────────────────────────────────────────────

def test_pull_image_returns_id():
    mock_client = MagicMock()
    mock_img = MagicMock()
    mock_img.id = "sha256:abc123def456"
    mock_client.images.pull.return_value = mock_img

    orch = _make_orch(mock_client)
    result = orch.pull_image("cooperage-image-analyzer:latest")

    assert result == "sha256:abc123def456"
    mock_client.images.pull.assert_called_once_with("cooperage-image-analyzer:latest")


def test_image_exists_true():
    mock_client = MagicMock()
    orch = _make_orch(mock_client)
    assert orch.image_exists("cooperage-image-analyzer:latest") is True
    mock_client.images.get.assert_called_once_with("cooperage-image-analyzer:latest")


def test_image_exists_false():
    import docker.errors
    mock_client = MagicMock()
    mock_client.images.get.side_effect = docker.errors.ImageNotFound("nope")
    orch = _make_orch(mock_client)
    assert orch.image_exists("ghost:latest") is False


# ── create_volume ─────────────────────────────────────────────────────────────

def test_create_volume():
    mock_client = MagicMock()
    orch = _make_orch(mock_client)
    orch.create_volume("cooperage-session-abc")
    mock_client.volumes.create.assert_called_once_with(
        name="cooperage-session-abc", labels={"cooperage": "true"}
    )


# ── remove_volume ─────────────────────────────────────────────────────────────

def test_remove_volume():
    mock_client = MagicMock()
    orch = _make_orch(mock_client)
    orch.remove_volume("cooperage-session-abc")
    mock_client.volumes.get.assert_called_once_with("cooperage-session-abc")
    mock_client.volumes.get.return_value.remove.assert_called_once_with(force=True)


def test_remove_volume_ignores_not_found():
    import docker.errors
    mock_client = MagicMock()
    mock_client.volumes.get.side_effect = docker.errors.NotFound("gone")
    orch = _make_orch(mock_client)
    orch.remove_volume("cooperage-session-missing")  # should not raise


# ── start_container ───────────────────────────────────────────────────────────

@patch.object(DockerOrchestrator, "pick_free_port", return_value=9001)
def test_start_container_returns_container_info(_mock_port):
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "container123"
    mock_client.containers.get.side_effect = __import__("docker").errors.NotFound("no stale")
    mock_client.containers.run.return_value = mock_container

    orch = _make_orch(mock_client)
    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest", port=8000)
    info = orch.start_container(server_def, session)

    assert info.container_id == "container123"
    assert info.host_port == 9001
    assert info.server_name == "sim"
    assert info.session_id == session.id
    assert info.mcp_url == "http://localhost:9001"


@patch.object(DockerOrchestrator, "pick_free_port", return_value=9002)
def test_start_container_removes_stale(_mock_port):
    mock_client = MagicMock()
    stale = MagicMock()
    mock_client.containers.get.return_value = stale
    mock_client.containers.run.return_value = MagicMock(id="new123")

    orch = _make_orch(mock_client)
    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest")
    orch.start_container(server_def, session)

    stale.remove.assert_called_once_with(force=True)


@patch.object(DockerOrchestrator, "pick_free_port", return_value=9003)
def test_start_container_passes_env_and_volume(_mock_port):
    mock_client = MagicMock()
    mock_client.containers.get.side_effect = __import__("docker").errors.NotFound("x")
    mock_client.containers.run.return_value = MagicMock(id="abc")

    orch = _make_orch(mock_client)
    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest", env={"MY_VAR": "hello"})
    orch.start_container(server_def, session)

    _, kwargs = mock_client.containers.run.call_args
    assert kwargs["environment"]["MY_VAR"] == "hello"
    assert kwargs["environment"]["COOPERAGE_SESSION_ID"] == session.id
    assert session.volume_name in kwargs["volumes"]


# ── stop_container ────────────────────────────────────────────────────────────

def test_stop_container():
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.get.return_value = mock_container

    orch = _make_orch(mock_client)
    orch.stop_container("container123")

    mock_container.stop.assert_called_once_with(timeout=5)
    mock_container.remove.assert_called_once_with(force=True)


def test_stop_container_ignores_not_found():
    import docker.errors
    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("gone")

    orch = _make_orch(mock_client)
    orch.stop_container("ghost")  # should not raise


# ── wait_until_ready ──────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.base.time.sleep")
@patch("cooperage.orchestrator.base.httpx.get")
def test_wait_until_ready_succeeds(mock_get, _mock_sleep):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_get.return_value = mock_response

    info = ContainerInfo(container_id="c1", server_name="sim", session_id="s1", host_port=9001)

    orch = DockerOrchestrator()
    assert orch.wait_until_ready(info, timeout=5) is True


@patch("cooperage.orchestrator.base.time.monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6])
@patch("cooperage.orchestrator.base.time.sleep")
@patch("cooperage.orchestrator.base.httpx.get")
def test_wait_until_ready_times_out(mock_get, _mock_sleep, _mock_mono):
    import httpx
    mock_get.side_effect = httpx.RequestError("refused")

    info = ContainerInfo(container_id="c1", server_name="sim", session_id="s1", host_port=9001)

    orch = DockerOrchestrator()
    assert orch.wait_until_ready(info, timeout=5) is False

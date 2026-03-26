"""
Orchestrator tests — all Docker SDK calls are mocked.
We never touch a real Docker daemon here.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call
import pytest

from cooperage.core.models import ContainerInfo, ServerDef, Session


def _make_session(**kwargs) -> Session:
    defaults = dict(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    defaults.update(kwargs)
    return Session(**defaults)


# ── pull_image ────────────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.docker.get_client")
def test_pull_image_returns_id(mock_get_client):
    mock_client = MagicMock()
    mock_img = MagicMock()
    mock_img.id = "sha256:abc123def456"
    mock_client.images.pull.return_value = mock_img
    mock_get_client.return_value = mock_client

    from cooperage.orchestrator.docker import pull_image
    result = pull_image("cooperage-analysis:latest")

    assert result == "sha256:abc123def456"
    mock_client.images.pull.assert_called_once_with("cooperage-analysis:latest")


@patch("cooperage.orchestrator.docker.get_client")
def test_image_exists_true(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    from cooperage.orchestrator.docker import image_exists
    assert image_exists("cooperage-analysis:latest") is True
    mock_client.images.get.assert_called_once_with("cooperage-analysis:latest")


@patch("cooperage.orchestrator.docker.get_client")
def test_image_exists_false(mock_get_client):
    import docker.errors
    mock_client = MagicMock()
    mock_client.images.get.side_effect = docker.errors.ImageNotFound("nope")
    mock_get_client.return_value = mock_client

    from cooperage.orchestrator.docker import image_exists
    assert image_exists("ghost:latest") is False


# ── create_volume ─────────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.docker.get_client")
def test_create_volume(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    from cooperage.orchestrator.docker import create_volume
    create_volume("cooperage-session-abc")

    mock_client.volumes.create.assert_called_once_with(
        name="cooperage-session-abc", labels={"cooperage": "true"}
    )


# ── remove_volume ─────────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.docker.get_client")
def test_remove_volume(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    from cooperage.orchestrator.docker import remove_volume
    remove_volume("cooperage-session-abc")

    mock_client.volumes.get.assert_called_once_with("cooperage-session-abc")
    mock_client.volumes.get.return_value.remove.assert_called_once_with(force=True)


@patch("cooperage.orchestrator.docker.get_client")
def test_remove_volume_ignores_not_found(mock_get_client):
    import docker.errors
    mock_client = MagicMock()
    mock_client.volumes.get.side_effect = docker.errors.NotFound("gone")
    mock_get_client.return_value = mock_client

    from cooperage.orchestrator.docker import remove_volume
    remove_volume("cooperage-session-missing")  # should not raise


# ── start_container ───────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.docker._pick_free_port", return_value=9001)
@patch("cooperage.orchestrator.docker.get_client")
def test_start_container_returns_container_info(mock_get_client, _mock_port):
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "container123"
    mock_client.containers.get.side_effect = __import__("docker").errors.NotFound("no stale")
    mock_client.containers.run.return_value = mock_container
    mock_get_client.return_value = mock_client

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest", port=8000)

    from cooperage.orchestrator.docker import start_container
    info = start_container(server_def, session)

    assert info.container_id == "container123"
    assert info.host_port == 9001
    assert info.server_name == "sim"
    assert info.session_id == session.id
    assert info.mcp_url == "http://localhost:9001"


@patch("cooperage.orchestrator.docker._pick_free_port", return_value=9002)
@patch("cooperage.orchestrator.docker.get_client")
def test_start_container_removes_stale(mock_get_client, _mock_port):
    import docker.errors
    mock_client = MagicMock()
    stale = MagicMock()
    mock_client.containers.get.return_value = stale  # stale container exists
    mock_client.containers.run.return_value = MagicMock(id="new123")
    mock_get_client.return_value = mock_client

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest")

    from cooperage.orchestrator.docker import start_container
    start_container(server_def, session)

    stale.remove.assert_called_once_with(force=True)


@patch("cooperage.orchestrator.docker._pick_free_port", return_value=9003)
@patch("cooperage.orchestrator.docker.get_client")
def test_start_container_passes_env_and_volume(mock_get_client, _mock_port):
    import docker.errors
    mock_client = MagicMock()
    mock_client.containers.get.side_effect = __import__("docker").errors.NotFound("x")
    mock_client.containers.run.return_value = MagicMock(id="abc")
    mock_get_client.return_value = mock_client

    session = _make_session()
    server_def = ServerDef(name="sim", image="sim:latest", env={"MY_VAR": "hello"})

    from cooperage.orchestrator.docker import start_container
    start_container(server_def, session)

    _, kwargs = mock_client.containers.run.call_args
    assert kwargs["environment"]["MY_VAR"] == "hello"
    assert kwargs["environment"]["COOPERAGE_SESSION_ID"] == session.id
    assert session.volume_name in kwargs["volumes"]


# ── stop_container ────────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.docker.get_client")
def test_stop_container(mock_get_client):
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_get_client.return_value = mock_client

    from cooperage.orchestrator.docker import stop_container
    stop_container("container123")

    mock_container.stop.assert_called_once_with(timeout=5)
    mock_container.remove.assert_called_once_with(force=True)


@patch("cooperage.orchestrator.docker.get_client")
def test_stop_container_ignores_not_found(mock_get_client):
    import docker.errors
    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("gone")
    mock_get_client.return_value = mock_client

    from cooperage.orchestrator.docker import stop_container
    stop_container("ghost")  # should not raise


# ── wait_until_ready ──────────────────────────────────────────────────────────

@patch("cooperage.orchestrator.docker.time.sleep")
@patch("cooperage.orchestrator.docker.httpx.get")
def test_wait_until_ready_succeeds(mock_get, _mock_sleep):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_get.return_value = mock_response

    info = ContainerInfo(container_id="c1", server_name="sim", session_id="s1", host_port=9001)

    from cooperage.orchestrator.docker import wait_until_ready
    assert wait_until_ready(info, timeout=5) is True


@patch("cooperage.orchestrator.docker.time.monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6])
@patch("cooperage.orchestrator.docker.time.sleep")
@patch("cooperage.orchestrator.docker.httpx.get")
def test_wait_until_ready_times_out(mock_get, _mock_sleep, _mock_mono):
    import httpx
    mock_get.side_effect = httpx.RequestError("refused")

    info = ContainerInfo(container_id="c1", server_name="sim", session_id="s1", host_port=9001)

    from cooperage.orchestrator.docker import wait_until_ready
    assert wait_until_ready(info, timeout=5) is False

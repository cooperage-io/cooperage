from datetime import datetime, timedelta, timezone
from cooperage.core.models import Session, ServerDef, ContainerInfo


def test_session_auto_generates_id():
    s = Session(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert s.id
    assert len(s.id) == 32  # uuid4 hex


def test_session_auto_generates_volume_name():
    s = Session(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert s.volume_name == f"cooperage-session-{s.id}"


def test_session_two_instances_have_different_ids():
    t = datetime.now(timezone.utc) + timedelta(hours=1)
    a = Session(expires_at=t)
    b = Session(expires_at=t)
    assert a.id != b.id


def test_session_name_defaults_to_none():
    s = Session(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert s.name is None


def test_session_containers_default_empty():
    s = Session(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert s.containers == {}


def test_server_def_defaults():
    s = ServerDef(name="foo", image="foo:latest")
    assert s.port == 8000
    assert s.description == ""
    assert s.env == {}


def test_server_def_custom_fields():
    s = ServerDef(name="sim", image="sim:v2", port=9000, description="Sim server", env={"X": "1"})
    assert s.port == 9000
    assert s.env == {"X": "1"}


def test_container_info_mcp_url():
    c = ContainerInfo(container_id="abc", server_name="sim", session_id="s1", host_port=9042)
    assert c.mcp_url == "http://localhost:9042"

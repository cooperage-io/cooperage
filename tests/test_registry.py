import json
import pytest
from cooperage.core.models import ServerDef


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Point the registry at a temp file for every test."""
    from cooperage.core import config
    monkeypatch.setattr(config.settings, "registry_path", tmp_path / "registry.json")


def _reg():
    import cooperage.registry.registry as r
    return r


def test_load_empty_when_no_file():
    assert _reg().load() == []


def test_register_and_load():
    s = ServerDef(name="sim", image="sim:latest", description="Sim server")
    _reg().register(s)
    servers = _reg().load()
    assert len(servers) == 1
    assert servers[0].name == "sim"
    assert servers[0].image == "sim:latest"


def test_register_multiple():
    _reg().register(ServerDef(name="a", image="a:1"))
    _reg().register(ServerDef(name="b", image="b:1"))
    names = [s.name for s in _reg().load()]
    assert names == ["a", "b"]


def test_register_overwrites_existing_name():
    _reg().register(ServerDef(name="sim", image="sim:v1"))
    _reg().register(ServerDef(name="sim", image="sim:v2"))
    servers = _reg().load()
    assert len(servers) == 1
    assert servers[0].image == "sim:v2"


def test_get_returns_correct_server():
    _reg().register(ServerDef(name="sim", image="sim:latest"))
    s = _reg().get("sim")
    assert s is not None
    assert s.name == "sim"


def test_get_returns_none_for_unknown():
    assert _reg().get("nonexistent") is None


def test_deregister_removes_entry():
    _reg().register(ServerDef(name="sim", image="sim:latest"))
    ok = _reg().deregister("sim")
    assert ok is True
    assert _reg().load() == []


def test_deregister_returns_false_when_not_found():
    ok = _reg().deregister("ghost")
    assert ok is False


def test_registry_persists_as_valid_json(tmp_path, monkeypatch):
    from cooperage.core import config
    path = tmp_path / "registry.json"
    monkeypatch.setattr(config.settings, "registry_path", path)
    _reg().register(ServerDef(name="sim", image="sim:latest", env={"KEY": "val"}))
    data = json.loads(path.read_text())
    assert data[0]["env"] == {"KEY": "val"}


# ── Malformed registry.json ────────────────────────────────────────────────


def test_load_returns_empty_on_invalid_json(tmp_path, monkeypatch):
    """Invalid JSON in registry.json should return an empty list, not crash."""
    from cooperage.core import config
    path = tmp_path / "registry.json"
    path.write_text("{not valid json!!!")
    monkeypatch.setattr(config.settings, "registry_path", path)
    assert _reg().load() == []


def test_load_skips_entries_missing_required_fields(tmp_path, monkeypatch):
    """Entries missing the 'name' field should be skipped."""
    from cooperage.core import config
    path = tmp_path / "registry.json"
    path.write_text(json.dumps([
        {"image": "no-name:latest"},  # missing 'name'
        {"name": "valid", "image": "valid:latest"},
    ]))
    monkeypatch.setattr(config.settings, "registry_path", path)
    servers = _reg().load()
    assert len(servers) == 1
    assert servers[0].name == "valid"


def test_get_returns_none_for_malformed_entry(tmp_path, monkeypatch):
    """get() should return None when the matching entry fails validation."""
    from cooperage.core import config
    path = tmp_path / "registry.json"
    # Entry has name but missing other things that might cause issues - actually
    # ServerDef is lenient, so we need an entry where name matches but something
    # else is broken. Use an invalid type for a field.
    path.write_text(json.dumps([
        {"name": "broken", "port": "not-an-int"},
    ]))
    monkeypatch.setattr(config.settings, "registry_path", path)
    result = _reg().get("broken")
    assert result is None

import json
from pathlib import Path
from cooperage.core.models import ServerDef
from cooperage.core.config import settings


def _load_raw() -> list[dict]:
    path: Path = settings.registry_path
    if not path.exists():
        return []
    return json.loads(path.read_text())


def load() -> list[ServerDef]:
    return [ServerDef(**entry) for entry in _load_raw()]


def get(name: str) -> ServerDef | None:
    for entry in _load_raw():
        if entry["name"] == name:
            return ServerDef(**entry)
    return None


def register(server: ServerDef) -> None:
    path: Path = settings.registry_path
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = _load_raw()
    entries = [e for e in entries if e["name"] != server.name]
    entries.append(server.model_dump())
    path.write_text(json.dumps(entries, indent=2))


def deregister(name: str) -> bool:
    path: Path = settings.registry_path
    entries = _load_raw()
    new_entries = [e for e in entries if e["name"] != name]
    if len(new_entries) == len(entries):
        return False
    path.write_text(json.dumps(new_entries, indent=2))
    return True

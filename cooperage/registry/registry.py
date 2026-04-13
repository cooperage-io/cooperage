import json
import logging
from pathlib import Path
from cooperage.core.models import ServerDef
from cooperage.core.config import settings

logger = logging.getLogger(__name__)


def _load_raw() -> list[dict]:
    path: Path = settings.registry_path
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            logger.warning("registry.json is not a JSON array, ignoring")
            return []
        return data
    except Exception as e:
        logger.error("Failed to parse registry.json: %s", e)
        return []


def load() -> list[ServerDef]:
    results = []
    for entry in _load_raw():
        try:
            results.append(ServerDef(**entry))
        except Exception as e:
            logger.warning("Skipping invalid registry entry: %s", e)
    return results


def get(name: str) -> ServerDef | None:
    for entry in _load_raw():
        try:
            if entry.get("name") == name:
                return ServerDef(**entry)
        except Exception as e:
            logger.warning("Skipping invalid registry entry: %s", e)
    return None


def register(server: ServerDef) -> None:
    path: Path = settings.registry_path
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = _load_raw()
    entries = [e for e in entries if e.get("name") != server.name]
    entries.append(server.model_dump())
    path.write_text(json.dumps(entries, indent=2))


def deregister(name: str) -> bool:
    path: Path = settings.registry_path
    entries = _load_raw()
    new_entries = [e for e in entries if e.get("name") != name]
    if len(new_entries) == len(entries):
        return False
    path.write_text(json.dumps(new_entries, indent=2))
    return True

from types import ModuleType


def get_orchestrator() -> ModuleType:
    from cooperage.core.config import settings
    if settings.orchestrator == "kubernetes":
        from cooperage.orchestrator import kubernetes
        return kubernetes
    from cooperage.orchestrator import docker
    return docker

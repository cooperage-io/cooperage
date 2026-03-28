from cooperage.orchestrator.base import Orchestrator

_instance: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _instance
    if _instance is None:
        from cooperage.core.config import settings
        if settings.orchestrator == "kubernetes":
            from cooperage.orchestrator.kubernetes import KubernetesOrchestrator
            _instance = KubernetesOrchestrator()
        else:
            from cooperage.orchestrator.docker import DockerOrchestrator
            _instance = DockerOrchestrator()
    return _instance

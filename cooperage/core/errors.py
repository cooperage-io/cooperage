"""Structured error types for Cooperage.

Every error carries a machine-readable code, a human-readable message,
a retriable flag, and an optional suggestion the LLM can act on.
"""


class CooperageError(Exception):
    """Base error for all Cooperage operations."""

    code: str = "internal_error"
    retriable: bool = False
    suggestion: str | None = None

    def __init__(self, message: str, *, retriable: bool | None = None, suggestion: str | None = None):
        super().__init__(message)
        if retriable is not None:
            self.retriable = retriable
        if suggestion is not None:
            self.suggestion = suggestion

    def to_dict(self) -> dict:
        d = {
            "error": True,
            "code": self.code,
            "message": str(self),
            "retriable": self.retriable,
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


class ServerNotFoundError(CooperageError):
    code = "server_not_found"
    retriable = False
    suggestion = "Check available servers with cooperage_list_servers."


class SessionNotFoundError(CooperageError):
    code = "session_not_found"
    retriable = False
    suggestion = "Create a new session with cooperage_create_session."


class ContainerStartupError(CooperageError):
    """Container failed to start or become ready."""
    code = "container_startup_failed"
    retriable = True
    suggestion = "Retry the tool call — the container may need more time. If this persists, try cooperage_pull_server to pre-pull the image."

    def __init__(self, message: str, *, logs: str | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.logs = logs

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.logs:
            d["container_logs"] = self.logs
        return d


class ImagePullError(CooperageError):
    code = "image_pull_failed"
    retriable = True
    suggestion = "Check that the image name and tag are correct. For private registries, verify credentials."


class ToolExecutionError(CooperageError):
    """The remote MCP server returned an error."""
    code = "tool_execution_error"
    retriable = False
    suggestion = "Check the tool arguments and try again."


class ContainerConnectionError(CooperageError):
    """Cannot reach the container's MCP endpoint."""
    code = "container_connection_error"
    retriable = True
    suggestion = "The container may still be starting. Retry in a few seconds."


class QuotaExceededError(CooperageError):
    code = "quota_exceeded"
    retriable = False
    suggestion = "End an existing session with cooperage_end_session before creating a new one."

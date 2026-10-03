from __future__ import annotations

from typing import Any


class ToolResult(str):
    """Human-readable tool result that also preserves execution status.

    Subclassing ``str`` keeps existing logging and display code compatible while
    allowing tool boundaries to pass an explicit success/error payload.
    """

    def __new__(
        cls,
        message: str,
        *,
        success: bool = True,
        error: str | None = None,
        error_code: str | None = None,
        data: Any = None,
    ) -> "ToolResult":
        result = super().__new__(cls, str(message))
        result.success = bool(success)
        result.error = error
        result.error_code = error_code
        result.data = data
        return result

    def __init__(
        self,
        message: str,
        *,
        success: bool = True,
        error: str | None = None,
        error_code: str | None = None,
        data: Any = None,
    ) -> None:
        # All fields are initialized in __new__; accepting the same signature
        # prevents ``str.__init__`` from rejecting the keyword-only metadata.
        pass

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": self.success,
            "message": str(self),
            "result": str(self),
        }
        if self.error is not None:
            payload["error"] = self.error
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.data is not None:
            payload["data"] = self.data
        return payload

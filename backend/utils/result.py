from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Result(Generic[T]):
    """Success/failure envelope. Keeps failures from reaching an LLM as if they were data."""

    success: bool
    value: T | None = None
    code: str | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, value: T, **meta: Any) -> Result[T]:
        return cls(success=True, value=value, meta=meta)

    @classmethod
    def fail(cls, code: str, error: str, **meta: Any) -> Result[T]:
        return cls(success=False, code=code, error=error, meta=meta)

    def __bool__(self) -> bool:
        return self.success

    def as_tool_message(self) -> str:
        if self.success:
            return f"OK: {self.value}"
        return f"ERROR[{self.code}]: {self.error}"

"""业务操作的可预期冲突。"""
from __future__ import annotations

from typing import Any


class ConflictError(Exception):
    """请求基于过期版本，不能覆盖其他请求已经提交的内容。"""

    def __init__(self, message: str, current: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.current = current

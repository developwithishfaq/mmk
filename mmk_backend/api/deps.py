"""FastAPI dependencies — override in tests via ``app.dependency_overrides``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException

from mmk_backend.contracts import TradingRuntime
from mmk_backend.runtime_state import RuntimeState, get_runtime_state
from mmk_api import Session


def get_runtime() -> RuntimeState:
    return get_runtime_state()


RuntimeDep = Annotated[RuntimeState, Depends(get_runtime)]


def require_session() -> Session:
    """Raise 503 when no active broker session exists."""
    rt = get_runtime_state()
    if rt.session is None:
        raise HTTPException(503, "Not connected — please log in first")
    return rt.session


SessionDep = Annotated[Session, Depends(require_session)]

__all__ = [
    "get_runtime",
    "RuntimeDep",
    "TradingRuntime",
    "require_session",
    "SessionDep",
]

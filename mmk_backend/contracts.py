"""
Typing protocols for the live trading runtime.

Implementations: :class:`mmk_backend.runtime_state.RuntimeState` (production). In tests, use a
``Protocol``-compatible stand-in or ``unittest.mock.Mock(spec_set=TradingRuntime)``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TradingRuntime(Protocol):
    """Narrow surface used by HTTP layer and hooks; extend as you split more logic into services."""

    user_id: str
    password: str
    pin: str
    session: Any
    sockets: Any
    orders: dict[str, dict]
    orders_lock: Any
    prices: dict[str, dict]
    prices_lock: Any
    web_sessions: set[str]
    web_sessions_lock: Any
    symbols: list[dict]
    symbols_lock: Any
    broker_view: Any
    broker_view_lock: Any
    login_lock: Any
    settings: Any

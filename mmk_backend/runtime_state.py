"""
Single mutable runtime for one server process.

HTTP handlers resolve ``RuntimeState`` via ``get_runtime`` (FastAPI Depends) so you can mock
the whole broker-facing surface with ``dependency_overrides`` in tests.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Optional

from mmk_backend.settings import Settings, load_settings

if TYPE_CHECKING:
    from broker_view import BrokerView
    from mmk_api import Session, SocketSet


class RuntimeState:
    __slots__ = (
        "settings",
        "user_id",
        "password",
        "pin",
        "session",
        "sockets",
        "login_lock",
        "prices",
        "prices_lock",
        "orders",
        "orders_lock",
        "web_sessions",
        "web_sessions_lock",
        "symbols",
        "symbols_lock",
        "broker_view",
        "broker_view_lock",
        # Live market status from `ht` socket message ("OPENED", "CLOSED", etc.)
        # Updated in real-time by socket_handler; faster than REST polling.
        "market_status",
        # True once feed manager confirms it is connected (`hf` message d=="1").
        "feed_alive",
        # Circuit-breaker limits: key = "MARKET_SYMBOL" (e.g. "01_OGDC"),
        # value = {"upper": float, "lower": float}.  Fetched on login.
        "cap_limits",
        "cap_limits_lock",
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.user_id = os.getenv("MMK_USER_ID", "RF991")
        self.password = os.getenv("MMK_PASSWORD", "68037430")
        self.pin = os.getenv("MMK_PIN", "1111")

        self.session: Session | None = None
        self.sockets: SocketSet | None = None
        self.login_lock = threading.Lock()

        self.prices: dict[str, dict] = {}
        self.prices_lock = threading.Lock()

        self.orders: dict[str, dict] = {}
        self.orders_lock = threading.Lock()

        self.web_sessions: set[str] = set()
        self.web_sessions_lock = threading.Lock()

        self.symbols: list[dict] = []
        self.symbols_lock = threading.Lock()

        self.broker_view: Optional["BrokerView"] = None
        self.broker_view_lock = threading.Lock()

        self.market_status: str = "UNKNOWN"
        self.feed_alive: bool = False
        self.cap_limits: dict[str, dict] = {}
        self.cap_limits_lock = threading.Lock()


_runtime_lock = threading.Lock()
_runtime: RuntimeState | None = None


def configure_runtime(rt: RuntimeState | None = None, *, reset: bool = False) -> RuntimeState:
    global _runtime
    with _runtime_lock:
        if reset:
            _runtime = None
        if rt is not None:
            _runtime = rt
        elif _runtime is None:
            _runtime = RuntimeState()
        return _runtime


def get_runtime_state() -> RuntimeState:
    return configure_runtime()


runtime = get_runtime_state()

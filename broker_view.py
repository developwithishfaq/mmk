"""
broker_view.py — Cached, thread-safe read-side queries against the broker.

Purpose
=======

The Daily Auto Trader needs to make many "is the broker in state X?" decisions
on every loop tick (Is there an outstanding order on AHCL?  Did I already
fill an order on AHCL today?  Is the market OPEN?  How much cash do I have?).

If we hit the broker REST API on every check, we'd hammer the connection and
slow the bot to a crawl. ``BrokerView`` wraps each broker query in a TTL-based
cache and returns fresh-enough snapshots.

Concurrency
-----------
Every public method acquires ``self._lock``; the loop thread, the on-demand
``refresh()`` call, and the FastAPI request thread can all read concurrently.

Data sources
------------
- ``mmk_api.get_outstanding_orders``  (TTL = 5s)
- ``mmk_api.get_activity_logs``       (TTL = 10s)
- ``mmk_api.get_open_positions``      (TTL = 15s)
- ``mmk_api.get_available_cash``      (TTL = 30s)
- ``mmk_api.get_market_status``       (TTL = 30s)

All queries run against the active broker session; the caller injects a
``session_provider`` callable so this module never imports ``server`` and
never holds a stale ``Session`` reference.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from mmk_api import (
    Session,
    get_outstanding_orders,
    get_activity_logs,
    get_open_positions,
    get_available_cash,
    get_market_status,
)

log = logging.getLogger("mmk.broker_view")

# ── TTLs (seconds) ──────────────────────────────────────────────────
TTL_OUTSTANDING = 5
TTL_ACTIVITY    = 10
TTL_POSITIONS   = 15
TTL_CASH        = 30
TTL_MARKET      = 30


class BrokerView:
    """Cached snapshot of broker state with explicit TTLs per query."""

    def __init__(
        self,
        session_provider: Callable[[], Optional[Session]],
        pin_provider:     Callable[[], str] = lambda: "",
    ):
        self._session_provider = session_provider
        self._pin_provider = pin_provider
        self._lock = threading.RLock()

        self._outstanding: list[dict] = []
        self._outstanding_ts: float = 0.0

        self._activity: list[dict] = []
        self._activity_ts: float = 0.0

        self._positions: list[dict] = []
        self._positions_ts: float = 0.0

        self._cash: float = 0.0
        self._cash_ts: float = 0.0

        self._market_status: str = "UNKNOWN"
        self._market_status_ts: float = 0.0

    # ── Internal: refresh helpers ───────────────────────────────────

    def _session(self) -> Optional[Session]:
        try:
            return self._session_provider()
        except Exception:
            return None

    def _pin(self) -> str:
        try:
            return self._pin_provider() or ""
        except Exception:
            return ""

    def _refresh_outstanding(self) -> None:
        s = self._session()
        if s is None:
            return
        try:
            rows = get_outstanding_orders(s) or []
        except Exception as e:
            log.warning(f"[API FAIL] get_outstanding_orders: {e}")
            return
        with self._lock:
            self._outstanding = rows if isinstance(rows, list) else []
            self._outstanding_ts = time.time()
        log.info(f"[API OK] get_outstanding_orders: {len(self._outstanding)} orders")

    def _refresh_activity(self) -> None:
        s = self._session()
        if s is None:
            return
        try:
            rows = get_activity_logs(s) or []
        except Exception as e:
            log.warning(f"[API FAIL] get_activity_logs: {e}")
            return
        with self._lock:
            self._activity = rows if isinstance(rows, list) else []
            self._activity_ts = time.time()
        log.info(f"[API OK] get_activity_logs: {len(self._activity)} rows")

    def _refresh_positions(self) -> None:
        s = self._session()
        if s is None:
            return
        try:
            rows = get_open_positions(s) or []
        except Exception as e:
            log.warning(f"[API FAIL] get_open_positions: {e}")
            return
        with self._lock:
            self._positions = rows if isinstance(rows, list) else []
            self._positions_ts = time.time()
        syms = [r.get("symbol", "?") for r in self._positions]
        log.info(f"[API OK] get_open_positions: {len(self._positions)} positions  {syms}")

    def _refresh_cash(self) -> None:
        s = self._session()
        if s is None:
            return
        try:
            data = get_available_cash(s, pin=self._pin())
            cash = data.get("available_cash") if isinstance(data, dict) else None
        except Exception as e:
            log.warning(f"[API FAIL] get_available_cash: {e}")
            return
        with self._lock:
            self._cash = float(cash or 0.0)
            self._cash_ts = time.time()
        log.info(f"[API OK] get_available_cash: {self._cash:.2f} PKR")

    def _refresh_market(self) -> None:
        s = self._session()
        if s is None:
            return
        try:
            data = get_market_status(s)
            label = data.get("status") if isinstance(data, dict) else None
        except Exception as e:
            log.warning(f"[API FAIL] get_market_status: {e}")
            return
        with self._lock:
            self._market_status = (label or "UNKNOWN").upper()
            self._market_status_ts = time.time()
        log.info(f"[API OK] get_market_status: {self._market_status}")

    def _maybe_refresh(self, kind: str) -> None:
        now = time.time()
        with self._lock:
            ts_map = {
                "outstanding": (self._outstanding_ts, TTL_OUTSTANDING),
                "activity":    (self._activity_ts,    TTL_ACTIVITY),
                "positions":   (self._positions_ts,   TTL_POSITIONS),
                "cash":        (self._cash_ts,        TTL_CASH),
                "market":      (self._market_status_ts, TTL_MARKET),
            }
            ts, ttl = ts_map.get(kind, (0.0, 0))
            stale = (now - ts) >= ttl
        if not stale:
            return
        if kind == "outstanding":
            self._refresh_outstanding()
        elif kind == "activity":
            self._refresh_activity()
        elif kind == "positions":
            self._refresh_positions()
        elif kind == "cash":
            self._refresh_cash()
        elif kind == "market":
            self._refresh_market()

    # ── Public read API ─────────────────────────────────────────────

    def outstanding_for(self, symbol: str) -> list[dict]:
        """Return outstanding orders matching `symbol` (case-insensitive)."""
        self._maybe_refresh("outstanding")
        sym = (symbol or "").upper().strip()
        with self._lock:
            return [
                o for o in self._outstanding
                if (o.get("SECURITY_SYMBOL") or "").upper() == sym
            ]

    def activity_today_for(self, symbol: str) -> list[dict]:
        """Return today's activity log rows matching `symbol`."""
        self._maybe_refresh("activity")
        sym = (symbol or "").upper().strip()
        with self._lock:
            return [
                a for a in self._activity
                if (a.get("symbol") or "").upper() == sym
            ]

    def activity_today_all(self) -> list[dict]:
        """Return today's full activity log (all symbols, all statuses)."""
        self._maybe_refresh("activity")
        with self._lock:
            return list(self._activity)

    # Statuses that mean the order actually worked (fully or partially).
    # An order with one of these statuses means capital is deployed or
    # a real position exists — block a second entry on the same symbol.
    _BLOCKING_STATUSES = frozenset({
        "filled", "partial", "partial_fill", "queued", "working",
        "new", "open",
    })

    # Statuses that mean the order is dead and had zero economic effect.
    # These must NOT block a fresh entry attempt on the same symbol.
    _NON_BLOCKING_STATUSES = frozenset({
        "rejected", "cancelled", "canceled", "expired", "unknown", "",
    })

    def has_blocking_order_for(self, symbol: str) -> bool:
        """
        True if there is any reason to block a new entry on `symbol`:

        1. There is a currently **outstanding** (live, working) order whose
           status is not a terminal rejection — outstanding orders that are
           already rejected but not yet purged from the feed are excluded.

        2. There is a **filled or partially-filled** order in today's activity
           log, indicating capital is already deployed in this symbol.

        Cancelled and rejected orders — in either outstanding or activity —
        are explicitly treated as non-blocking so a failed attempt earlier in
        the session does not permanently lock out the symbol for the day.
        """
        if not symbol:
            return False

        # 1. Outstanding orders: skip any that are already terminal (rejected
        #    orders sometimes linger in the outstanding feed for a few seconds).
        for o in self.outstanding_for(symbol):
            status = (o.get("status") or o.get("ORDER_STATUS") or "").lower().strip()
            if status not in self._NON_BLOCKING_STATUSES:
                return True

        # 2. Activity log: only block on statuses that mean real execution.
        for a in self.activity_today_for(symbol):
            status = (a.get("status") or "").lower().strip()
            if status in self._BLOCKING_STATUSES:
                return True

        return False

    def has_any_today(self, symbol: str) -> bool:
        """
        True if there is ANY outstanding order OR ANY activity (queued/filled/
        partial/cancelled/rejected) on `symbol` today.

        NOTE: use has_blocking_order_for() for pre-entry gates — this method is
        too conservative because it permanently blocks re-entry on a symbol once
        any order (even a rejected one) has touched it today.
        """
        if not symbol:
            return False
        if self.outstanding_for(symbol):
            return True
        # Activity logs include queued, filled, partial, cancelled, rejected.
        return any(
            (a.get("status") or "").lower() not in ("", "unknown")
            for a in self.activity_today_for(symbol)
        )

    def open_positions(self) -> list[dict]:
        """Snapshot list of open positions from the broker."""
        self._maybe_refresh("positions")
        with self._lock:
            return list(self._positions)

    def cash(self) -> float:
        """Cached available cash (PKR)."""
        self._maybe_refresh("cash")
        with self._lock:
            return self._cash

    def market_status(self) -> str:
        """Cached human-readable market status (OPEN/CLOSED/HALTED/...)."""
        self._maybe_refresh("market")
        with self._lock:
            return self._market_status

    def is_market_open(self) -> bool:
        # Broker REST returns "OPENED" (with a D); socket `ht` may also send "OPEN" or "OHO".
        # Mirror socket_handler._OPEN_STATUSES so REST and socket paths agree.
        from mmk_backend.services.socket_handler import _OPEN_STATUSES
        return self.market_status() in _OPEN_STATUSES

    def refresh(self) -> dict:
        """Force-refresh every cache (used by manual Sync from Broker button)."""
        self._refresh_outstanding()
        self._refresh_activity()
        self._refresh_positions()
        self._refresh_cash()
        self._refresh_market()
        with self._lock:
            return {
                "ts":            time.time(),
                "outstanding":   len(self._outstanding),
                "activity":      len(self._activity),
                "positions":     len(self._positions),
                "cash":          self._cash,
                "market_status": self._market_status,
            }

    def snapshot(self) -> dict:
        """Read-only snapshot for diagnostics / UI."""
        with self._lock:
            return {
                "outstanding_count": len(self._outstanding),
                "outstanding_ts":    self._outstanding_ts,
                "activity_count":    len(self._activity),
                "activity_ts":       self._activity_ts,
                "positions_count":   len(self._positions),
                "positions_ts":      self._positions_ts,
                "cash":              self._cash,
                "cash_ts":           self._cash_ts,
                "market_status":     self._market_status,
                "market_ts":         self._market_status_ts,
            }

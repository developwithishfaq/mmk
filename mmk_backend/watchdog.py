"""
watchdog.py — Broker connection watchdog.

What it does
============
Runs a background thread that checks every CHECK_INTERVAL_SEC seconds.

Case 1 — No live session (server started before network was ready, or
          login failed at boot):
    Tries try_auto_login_from_disk() with a LOGIN_COOLDOWN_SEC cooldown so
    we don't hammer the broker with failed attempts.

Case 2 — Session alive but socket went silent (network dropped mid-day):
    If no WebSocket frame has been received for DEAD_AFTER_SEC seconds,
    declares the connection dead and calls try_auto_login_from_disk() to
    create a brand-new session + fresh sockets.

    If the daily trader was RUNNING when the drop happened, it is
    automatically restarted after a successful reconnect.

The watchdog is deliberately conservative:
- It never auto-starts the daily trader on first boot — only after a
  drop+reconnect where it was already confirmed running.
- LOGIN_COOLDOWN_SEC prevents runaway reconnect loops.
- last_message_ts is reset to 0 before each reconnect attempt so a slow
  reconnect doesn't immediately re-trigger the same path.
"""

from __future__ import annotations

import logging
import threading
import time

import daily_trader
from mmk_backend.runtime_state import runtime

log = logging.getLogger("mmk.watchdog")

# ── Tuning knobs ─────────────────────────────────────────────────────────────
CHECK_INTERVAL_SEC = 30    # how often the watchdog wakes up
DEAD_AFTER_SEC     = 120   # silence this long → declare socket dead
LOGIN_COOLDOWN_SEC = 90    # minimum gap between any two reconnect attempts


class ConnectionWatchdog:
    """Background thread that monitors and auto-recovers the broker connection."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop   = threading.Event()
        self._last_attempt: float = 0.0   # ts of last reconnect attempt

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="mmk-watchdog", daemon=True,
        )
        self._thread.start()
        log.info("[watchdog] started — checking every %ss, dead after %ss",
                 CHECK_INTERVAL_SEC, DEAD_AFTER_SEC)

    def stop(self) -> None:
        self._stop.set()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.wait(CHECK_INTERVAL_SEC):
            try:
                self._check()
            except Exception as exc:
                log.warning("[watchdog] check error: %s", exc)

    def _check(self) -> None:
        # Deferred imports avoid circular references at module load time.
        from mmk_backend.services.auth_service import (   # noqa: PLC0415
            _load_session_state_file,
            try_auto_login_from_disk,
        )

        # Only act when we have stored credentials to reconnect with.
        state = _load_session_state_file()
        if not state.get("last_login"):
            return

        now = time.time()

        # ── Case 1: no live session ───────────────────────────────────────
        if runtime.session is None:
            if now - self._last_attempt < LOGIN_COOLDOWN_SEC:
                return   # still cooling down
            self._last_attempt = now
            log.info("[watchdog] No session — attempting auto-login")
            ok = try_auto_login_from_disk()
            if ok:
                log.info("[watchdog] Session restored on boot")
            else:
                log.warning("[watchdog] Auto-login failed — will retry in %ss",
                            LOGIN_COOLDOWN_SEC)
            return

        # ── Case 2: has session — check socket liveness ───────────────────
        last_msg = runtime.last_message_ts
        if last_msg == 0:
            # Session exists but no frame received yet (first few seconds
            # of a fresh connection — give it time).
            return

        silence = now - last_msg
        if silence < DEAD_AFTER_SEC:
            return   # alive and healthy

        # ── Socket is dead ────────────────────────────────────────────────
        if now - self._last_attempt < LOGIN_COOLDOWN_SEC:
            return   # don't hammer the broker

        bot         = daily_trader.get()
        was_running = (bot._state == "RUNNING")

        log.warning(
            "[watchdog] Socket silent for %ds — reconnecting "
            "(daily_trader=%s)",
            int(silence), bot._state,
        )

        # Reset ts BEFORE the attempt so a slow reconnect doesn't re-fire.
        runtime.last_message_ts = 0
        self._last_attempt = now

        ok = try_auto_login_from_disk()
        if not ok:
            log.error("[watchdog] Reconnect failed — will retry in %ss",
                      LOGIN_COOLDOWN_SEC)
            return

        log.info("[watchdog] Reconnected to broker successfully")

        # Resume the daily trader only if it was actively running when the
        # drop happened — never auto-start it cold.
        if was_running and bot._state != "RUNNING":
            result = bot.start()
            log.info("[watchdog] Daily trader auto-resumed: %s", result)


# ── Module-level singleton ────────────────────────────────────────────────────

_watchdog = ConnectionWatchdog()


def start_watchdog() -> None:
    _watchdog.start()


def stop_watchdog() -> None:
    _watchdog.stop()

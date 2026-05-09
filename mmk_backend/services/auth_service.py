"""
auth_service.py — web-session token lifecycle and session-state persistence.

Responsibilities
----------------
- Reading / writing web_session_state.json (tokens + last-login credentials).
- Issuing and revoking browser session tokens.
- Extracting the session token from a FastAPI Request (cookie or header).
- Checking whether a request is authenticated.
- Auto-login on server restart from persisted credentials.
- refresh_subscriptions() — merges watchlist + auto-trade symbols after
  login or strategy CRUD, and re-wires daily-trader hooks.

Circular-import note
--------------------
try_auto_login_from_disk() needs socket_handler.on_message to pass to
connect_all_sockets().  socket_handler imports auto_trade_service (not this
module), so there is no module-level cycle — the import is deferred inside
the function body.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time

from fastapi import Request

import auto_trade
from mmk_api import (
    connect_all_sockets,
    disconnect_all_sockets,
    get_symbol_list,
    get_watchlist,
    login,
    subscribe_watchlist,
)
from mmk_backend.runtime_state import runtime
from mmk_backend.services.daily_trader_hooks import install_hooks

log = logging.getLogger("mmk.auth_svc")

SESSION_COOKIE_NAME = "mmk_ui_session"
_SESSION_HEADER_ALT = "x-mmk-session"


# ── Session-state file helpers ──────────────────────────────────────────────

def _save_session_state_file(state: dict) -> None:
    path = runtime.settings.session_state_file
    tmp  = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def _load_session_state_file() -> dict:
    path = runtime.settings.session_state_file
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ── Public persistence API ──────────────────────────────────────────────────

def persist_auth_state() -> None:
    with runtime.web_sessions_lock:
        tokens = sorted(runtime.web_sessions)
    state = _load_session_state_file()
    state["web_sessions"] = tokens
    state["updated_at"]   = int(time.time())
    _save_session_state_file(state)


def persist_login_credentials(user_id: str, password: str, pin: str) -> None:
    state = _load_session_state_file()
    state["last_login"] = {"user_id": user_id, "password": password, "pin": pin}
    _save_session_state_file(state)


def clear_persisted_auth() -> None:
    state = _load_session_state_file()
    state["web_sessions"] = []
    state.pop("last_login", None)
    _save_session_state_file(state)


def restore_web_sessions_from_disk() -> None:
    state  = _load_session_state_file()
    tokens = state.get("web_sessions", [])
    if not isinstance(tokens, list):
        return
    clean = {str(t).strip() for t in tokens if str(t).strip()}
    if not clean:
        return
    with runtime.web_sessions_lock:
        runtime.web_sessions.update(clean)


def try_auto_login_from_disk() -> bool:
    # Deferred import: socket_handler → auto_trade_service (no cycle back here).
    from mmk_backend.services.socket_handler import on_message  # noqa: PLC0415

    state      = _load_session_state_file()
    login_data = state.get("last_login", {})
    if not isinstance(login_data, dict):
        return False
    user_id  = str(login_data.get("user_id",  "")).strip()
    password = str(login_data.get("password", "")).strip()
    pin      = str(login_data.get("pin",      "")).strip()
    if not user_id or not password:
        return False
    try:
        with runtime.login_lock:
            runtime.user_id  = user_id
            runtime.password = password
            if pin:
                runtime.pin = pin
            runtime.session = login(user_id, password)
            runtime.sockets = connect_all_sockets(runtime.session, on_message=on_message)
            refresh_subscriptions()
            symbols = get_symbol_list(runtime.session)
            with runtime.symbols_lock:
                runtime.symbols.clear()
                runtime.symbols.extend(symbols)
        log.info(f"Auto-login restored for user {user_id}.")
        return True
    except Exception as e:
        log.warning(f"Auto-login restore failed: {e}")
        return False


def refresh_subscriptions() -> None:
    """
    Merge broker watchlist + auto-trade symbols and re-subscribe the MF socket.
    Also installs daily-trader hooks so the bot sees the fresh session.
    Called after login and after any auto-trade strategy CRUD.
    """
    if not runtime.session or not runtime.sockets or not runtime.sockets.mf:
        return
    try:
        wl = get_watchlist(runtime.session)
    except Exception:
        wl = []
    auto_syms = auto_trade.collect_auto_symbols()
    merged    = sorted({s.strip().upper() for s in wl + auto_syms if s and str(s).strip()})
    if merged:
        subscribe_watchlist(runtime.sockets, merged)
        log.info(f"Subscribed MF feed to {len(merged)} symbols (watchlist + auto-trade)")
    try:
        install_hooks()
    except Exception as e:
        log.warning(f"daily_trader hook install failed: {e}")


# ── Token lifecycle ─────────────────────────────────────────────────────────

def issue_web_session_token() -> str:
    token = secrets.token_urlsafe(32)
    with runtime.web_sessions_lock:
        runtime.web_sessions.add(token)
    persist_auth_state()
    return token


def revoke_web_session_token(token: str) -> None:
    with runtime.web_sessions_lock:
        runtime.web_sessions.discard(token)
    persist_auth_state()


# ── Request auth helpers ────────────────────────────────────────────────────

def web_session_token_from_request(req: Request) -> str:
    """Prefer cookie (browser UI), then Bearer / X-MMK-Session header."""
    t = (req.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if t:
        return t
    auth = (
        req.headers.get("authorization") or
        req.headers.get("Authorization") or ""
    ).strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (req.headers.get(_SESSION_HEADER_ALT) or "").strip()


def is_authenticated(req: Request) -> bool:
    token = web_session_token_from_request(req)
    if not token:
        return False
    with runtime.web_sessions_lock:
        token_ok = token in runtime.web_sessions
    if not token_ok:
        return False
    if runtime.session is None:
        try_auto_login_from_disk()
    return runtime.session is not None


def session_user_name() -> str:
    if runtime.session and isinstance(runtime.session.raw, dict):
        a_data = runtime.session.raw.get("aData", {})
        if isinstance(a_data, dict):
            return a_data.get("userName", runtime.session.user_id)
    return runtime.session.user_id if runtime.session else ""

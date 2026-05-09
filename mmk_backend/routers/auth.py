"""
auth.py — Authentication and session routes.

Endpoints
---------
POST /auth/login
POST /auth/logout
GET  /auth/me
GET  /session/info
GET  /              root — login page or dashboard
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from mmk_api import connect_all_sockets, disconnect_all_sockets, get_symbol_list, login, logout
from mmk_backend.runtime_state import runtime
from mmk_backend.schemas import LoginRequest
from mmk_backend.services import auth_service
from mmk_backend.services.socket_handler import on_message

log = logging.getLogger("mmk.router.auth")

router = APIRouter()


@router.post("/auth/login")
def auth_login(req: LoginRequest):
    try:
        with runtime.login_lock:
            if runtime.sockets:
                disconnect_all_sockets(runtime.sockets)
                runtime.sockets = None
                runtime.session = None
            runtime.user_id  = req.user_id
            runtime.password = req.password
            runtime.pin      = req.pin
            runtime.session  = login(req.user_id, req.password)
            runtime.sockets  = connect_all_sockets(runtime.session, on_message=on_message)
            auth_service.refresh_subscriptions()
            symbols = get_symbol_list(runtime.session)
            with runtime.symbols_lock:
                runtime.symbols.clear()
                runtime.symbols.extend(symbols)
    except Exception as e:
        raise HTTPException(401, f"Login failed: {e}") from e

    auth_service.persist_login_credentials(req.user_id, req.password, req.pin)
    token = auth_service.issue_web_session_token()

    resp = JSONResponse({
        "status":        "ok",
        "user_id":       runtime.session.user_id,
        "user_name":     auth_service.session_user_name(),
        "market_status": runtime.session.mkt_stat,
        "session_token": token,
    })
    resp.set_cookie(
        key=auth_service.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return resp


@router.post("/auth/logout")
def auth_logout(req: Request):
    token = auth_service.web_session_token_from_request(req)
    if token:
        auth_service.revoke_web_session_token(token)
    with runtime.web_sessions_lock:
        runtime.web_sessions.clear()
    auth_service.clear_persisted_auth()

    if runtime.session:
        try:
            logout(runtime.session)
        except Exception as e:
            log.warning(f"Logout API call failed: {e}")
    if runtime.sockets:
        try:
            disconnect_all_sockets(runtime.sockets)
        except Exception:
            pass

    runtime.sockets = None
    runtime.session = None

    resp = JSONResponse({"status": "logged_out"})
    resp.delete_cookie(auth_service.SESSION_COOKIE_NAME)
    return resp


@router.get("/auth/me")
def auth_me(req: Request):
    if not auth_service.is_authenticated(req):
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user_id":       runtime.session.user_id,
        "user_name":     auth_service.session_user_name(),
        "market_status": runtime.session.mkt_stat,
    }


@router.get("/session/info")
def session_info(req: Request):
    if not auth_service.is_authenticated(req) or runtime.session is None:
        raise HTTPException(503, "Not connected")
    return {
        "user_id":       runtime.session.user_id,
        "user_name":     auth_service.session_user_name(),
        "market_status": runtime.session.mkt_stat,
    }


@router.get("/")
def root(req: Request):
    """Serve the login page to anonymous visitors, the dashboard when authenticated."""
    ui = runtime.settings.ui_dir
    if auth_service.is_authenticated(req) and runtime.session is not None:
        resp = FileResponse(path=f"{ui}/index.html", media_type="text/html")
    else:
        resp = FileResponse(path=f"{ui}/login.html", media_type="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp

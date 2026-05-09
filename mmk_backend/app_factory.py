"""
Assemble middleware and static mounts. Keeps deployment concerns out of route modules.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from mmk_backend.middleware.api_logging import ApiLoggingMiddleware
from mmk_backend.settings import Settings, load_settings


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


def _cors_settings(raw: str) -> tuple[list[str], bool]:
    parts = [o.strip() for o in raw.split(",") if o.strip()]
    if not parts:
        parts = ["*"]
    allow_credentials = "*" not in parts
    return parts, allow_credentials


def attach_standard_middleware(app: FastAPI, settings: Settings | None = None) -> Settings:
    """
    API hit logging (JSONL) + CORS. Returns resolved Settings for callers that need paths.

    Middleware order matches previous ``server``: logging inner, CORS outer.
    """
    s = settings or load_settings()
    skip = ("/ui/", "/watchlist/csv")
    app.add_middleware(
        ApiLoggingMiddleware,
        api_log_path=s.api_log,
        skip_path_prefixes=skip,
    )
    origins, allow_cred = _cors_settings(s.cors_origins_raw)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_cred,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return s


def mount_ui(app: FastAPI, ui_dir: str | None = None, *, settings: Settings | None = None) -> None:
    """Serve optional static UI under /ui."""

    directory = ui_dir if ui_dir is not None else (settings or load_settings()).ui_dir
    app.mount("/ui", NoCacheStaticFiles(directory=directory), name="ui")


def trading_data_exists(settings: Settings | None = None) -> bool:
    """Whether ``web_session_state.json`` exists on disk."""

    path = (settings or load_settings()).session_state_file
    return os.path.exists(path)

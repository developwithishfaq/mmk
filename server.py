"""
server.py — FastAPI application entry point.

Wires together middleware, routers, and lifespan; contains no business logic.

Run:
    uvicorn server:app --reload
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler

import auto_trade
import daily_trader
from fastapi import FastAPI
from mmk_backend.app_factory import attach_standard_middleware, mount_ui
from mmk_backend.routers import account, admin, auth, daily_trader as dt_router, market, orders
from mmk_backend.routers import auto_trade as at_router
from mmk_backend.runtime_state import configure_runtime, runtime
from mmk_backend.services import auth_service
from mmk_backend.settings import LOG_DIR
from mmk_backend.watchdog import start_watchdog, stop_watchdog
from mmk_api import disconnect_all_sockets

# ── Logging ─────────────────────────────────────────────────────────────────
# Console: short timestamp for watching live.
# File:    full timestamp + logger name, rotates at midnight (PKT), keeps 14 days.
#          All bot decisions, order placements, fills, errors land in logs/trader.log
#          so you can review the full session after the fact.

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
))

_file_handler = TimedRotatingFileHandler(
    filename=os.path.join(LOG_DIR, "trader.log"),
    when="midnight",
    backupCount=14,      # keep 2 weeks of daily log files
    encoding="utf-8",
    utc=False,           # rotate at local midnight (PKT)
)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

logging.basicConfig(
    level=logging.INFO,
    handlers=[_console_handler, _file_handler],
)

configure_runtime(runtime)
auto_trade.init()
daily_trader.get().init()


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth_service.restore_web_sessions_from_disk()
    auth_service.try_auto_login_from_disk()
    start_watchdog()   # monitors broker connection; reconnects + resumes bot on drop
    yield
    stop_watchdog()
    if runtime.sockets:
        try:
            disconnect_all_sockets(runtime.sockets)
        except Exception:
            pass
    auto_trade.shutdown()
    daily_trader.get().shutdown()


app = FastAPI(title="MMK Trading API", lifespan=lifespan)

_settings = attach_standard_middleware(app, runtime.settings)
mount_ui(app, settings=_settings)

app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(market.router)
app.include_router(account.router)
app.include_router(at_router.router)
app.include_router(dt_router.router)
app.include_router(admin.router)

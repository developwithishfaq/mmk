"""
daily_trader.py — DailyTrader bot control and monitoring routes.

Endpoints
---------
GET   /daily-trader/status
POST  /daily-trader/start
POST  /daily-trader/stop
POST  /daily-trader/halt
POST  /daily-trader/reset-day
GET   /daily-trader/config
PATCH /daily-trader/config
GET   /daily-trader/signals
GET   /daily-trader/positions
GET   /daily-trader/trades
GET   /daily-trader/events
POST  /daily-trader/close
POST  /daily-trader/sync
GET   /daily-trader/broker-snapshot
GET   /daily-trader/report
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

import daily_trader
from mmk_backend.runtime_state import runtime
from mmk_backend.schemas import DailyTraderCloseRequest, DailyTraderConfigPatch, DailyTraderHaltRequest
from mmk_backend.services.daily_trader_hooks import get_broker_view

router = APIRouter(prefix="/daily-trader")


@router.get("/status")
def dt_status(req: Request):
    return daily_trader.get().status()


@router.post("/start")
def dt_start(req: Request):
    if runtime.session is None:
        raise HTTPException(401, "Not authenticated")
    return daily_trader.get().start()


@router.post("/stop")
def dt_stop(req: Request):
    return daily_trader.get().stop()


@router.post("/halt")
def dt_halt(req: Request, body: DailyTraderHaltRequest):
    return daily_trader.get().halt(body.reason or "manual")


@router.post("/reset-day")
def dt_reset_day(req: Request):
    return daily_trader.get().reset_day()


@router.get("/config")
def dt_get_config(req: Request):
    return daily_trader.get().get_config()


@router.patch("/config")
def dt_patch_config(req: Request, body: DailyTraderConfigPatch):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return daily_trader.get().set_config(patch)


@router.get("/signals")
def dt_signals(req: Request, limit: int = 50):
    return {"items": daily_trader.get().list_signals(limit=max(1, min(limit, 500)))}


@router.get("/positions")
def dt_positions(req: Request, include_closed: bool = False):
    return {"items": daily_trader.get().list_positions(include_closed=include_closed)}


@router.get("/trades")
def dt_trades(req: Request, limit: int = 50):
    return {"items": daily_trader.get().list_trades(limit=max(1, min(limit, 500)))}


@router.get("/events")
def dt_events(req: Request, limit: int = 100):
    return {"items": daily_trader.get().list_events(limit=max(1, min(limit, 500)))}


@router.post("/close")
def dt_close(req: Request, body: DailyTraderCloseRequest):
    return daily_trader.get().manual_close(body.pos_id, body.reason or "manual")


@router.post("/sync")
def dt_sync(req: Request):
    """Force-refresh broker caches and run a reconciliation pass."""
    return daily_trader.get().manual_sync()


@router.get("/broker-snapshot")
def dt_broker_snapshot(req: Request):
    """Diagnostic: BrokerView cache state (TTLs, last refresh timestamps)."""
    try:
        return get_broker_view().snapshot()
    except Exception as e:
        raise HTTPException(500, f"snapshot failed: {e}") from e


@router.get("/report")
def dt_report(req: Request):
    """End-of-day style performance summary."""
    bot    = daily_trader.get()
    trades = bot.list_trades(limit=500)
    closed = [t for t in trades if t.get("status") == "closed"]
    wins   = [t for t in closed if (t.get("realized_pl") or 0) > 0]
    losses = [t for t in closed if (t.get("realized_pl") or 0) < 0]
    gross_pl = round(sum((t.get("realized_pl") or 0) for t in closed), 2)
    return {
        "status":       bot.status(),
        "trades_count": len(closed),
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0,
        "gross_pl":     gross_pl,
        "avg_winner":   round(sum(t["realized_pl"] for t in wins)   / len(wins),   2) if wins   else 0,
        "avg_loser":    round(sum(t["realized_pl"] for t in losses) / len(losses), 2) if losses else 0,
        "trades":       closed,
    }

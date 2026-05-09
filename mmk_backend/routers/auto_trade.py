"""
auto_trade.py — Auto-trade strategy CRUD and test-tick routes.

Endpoints
---------
GET    /auto-trade/strategies
POST   /auto-trade/strategies
PATCH  /auto-trade/strategies/{strategy_id}
DELETE /auto-trade/strategies/{strategy_id}
POST   /auto-trade/strategies/{strategy_id}/reset
POST   /auto-trade/strategies/{strategy_id}/test-tick
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from websocket._exceptions import WebSocketConnectionClosedException

import auto_trade
from mmk_backend.runtime_state import runtime
from mmk_backend.schemas import AutoTradeCreate, AutoTradePatch, AutoTradeTestTick
from mmk_backend.services import auth_service
from mmk_backend.services.auto_trade_service import place_intent

router = APIRouter(prefix="/auto-trade")


@router.get("/strategies")
def auto_trade_list(req: Request):
    if not auth_service.is_authenticated(req):
        raise HTTPException(401, "Not authenticated")
    items = auto_trade.list_strategies()
    return {"count": len(items), "strategies": items}


@router.post("/strategies")
def auto_trade_create(req: Request, body: AutoTradeCreate):
    if not auth_service.is_authenticated(req):
        raise HTTPException(401, "Not authenticated")
    try:
        view = auto_trade.add_strategy(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    auth_service.refresh_subscriptions()
    return {"status": "ok", "strategy": view}


@router.patch("/strategies/{strategy_id}")
def auto_trade_patch(req: Request, strategy_id: str, body: AutoTradePatch):
    if not auth_service.is_authenticated(req):
        raise HTTPException(401, "Not authenticated")
    try:
        view = auto_trade.patch_strategy(
            strategy_id,
            {k: v for k, v in body.model_dump().items() if v is not None},
        )
    except KeyError:
        raise HTTPException(404, "strategy not found")
    auth_service.refresh_subscriptions()
    return {"status": "ok", "strategy": view}


@router.delete("/strategies/{strategy_id}")
def auto_trade_delete(req: Request, strategy_id: str):
    if not auth_service.is_authenticated(req):
        raise HTTPException(401, "Not authenticated")
    if not auto_trade.delete_strategy(strategy_id):
        raise HTTPException(404, "strategy not found")
    auth_service.refresh_subscriptions()
    return {"status": "deleted", "id": strategy_id}


@router.post("/strategies/{strategy_id}/reset")
def auto_trade_reset(req: Request, strategy_id: str):
    if not auth_service.is_authenticated(req):
        raise HTTPException(401, "Not authenticated")
    view = auto_trade.reset_strategy(strategy_id)
    if view is None:
        raise HTTPException(404, "strategy not found")
    return {"status": "ok", "strategy": view}


@router.post("/strategies/{strategy_id}/test-tick")
def auto_trade_test_tick(req: Request, strategy_id: str, body: AutoTradeTestTick):
    """
    Force-evaluate a strategy with a synthetic tick.
    Useful for verifying signal/position logic when the market is closed.
    Set place=true to actually submit any resulting order to the broker.
    """
    if not auth_service.is_authenticated(req):
        raise HTTPException(401, "Not authenticated")

    tick = {
        "last":       body.last,
        "low":        body.low,
        "high":       body.high,
        "prev_close": body.prev_close,
    }

    callback = None
    if body.place:
        if runtime.sockets is None or runtime.session is None:
            raise HTTPException(503, "Not connected to broker")

        def callback(intent):  # noqa: E306
            try:
                ord_hash = place_intent(intent)
            except WebSocketConnectionClosedException:
                raise
            with runtime.orders_lock:
                runtime.orders[ord_hash] = {
                    "ord_hash":         ord_hash,
                    "status":           "sent",
                    "symbol":           intent.symbol,
                    "side":             intent.side,
                    "auto_strategy_id": intent.strategy_id,
                    "created_ts":       time.time(),
                    "last_update_ts":   time.time(),
                }
            return ord_hash

    try:
        result = auto_trade.force_evaluate(
            strategy_id, tick,
            fallback_pin=runtime.pin,
            market_open=body.market_open,
            place_callback=callback,
        )
    except KeyError:
        raise HTTPException(404, "strategy not found")

    return result

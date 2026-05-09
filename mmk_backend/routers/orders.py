"""
orders.py — Order placement, cancellation, and query routes.

Endpoints
---------
POST /order/place
POST /order/cancel
POST /order/cancel/by-exch
POST /order/change
POST /order/place-slo
GET  /orders
GET  /orders/outstanding
GET  /orders/tradelogs
GET  /orders/activitylogs
GET  /orders/consolidated
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from websocket._exceptions import WebSocketConnectionClosedException

from mmk_api import (
    CANCEL_SIDE,
    MARKET_REG,
    cancel_order,
    change_order,
    get_activity_logs,
    get_consolidated_trade_logs,
    get_outstanding_orders,
    get_trade_logs,
    place_limit_buy,
    place_limit_sell,
    place_market_buy,
    place_market_sell,
    place_slo_order,
)
from mmk_backend.api.deps import SessionDep
from mmk_backend.runtime_state import runtime
from mmk_backend.schemas import (
    CancelByExchIdRequest,
    CancelOrderRequest,
    ChangeOrderRequest,
    PlaceOrderRequest,
    SloOrderRequest,
)

log = logging.getLogger("mmk.router.orders")

router = APIRouter()

_TERMINAL_ORDER_STATES = {"rejected", "filled", "cancelled"}


@router.post("/order/place")
def place_order(req: PlaceOrderRequest, session: SessionDep):
    """
    Place a limit or market buy/sell order.
    Returns ord_hash — use GET /orders or GET /orders/outstanding to retrieve
    the exch_order_id / house_order_id needed to cancel.
    """
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")

    log.info(
        f"[PLACE] symbol={req.symbol}  side={req.side}  "
        f"type={req.order_type}  price={req.price}  volume={req.volume}"
    )

    try:
        if req.order_type == "limit":
            fn = place_limit_buy if req.side == "buy" else place_limit_sell
            ord_hash = fn(
                runtime.sockets, session,
                req.symbol, req.price, req.volume, req.pin, req.market_type,
            )
        else:
            fn = place_market_buy if req.side == "buy" else place_market_sell
            ord_hash = fn(
                runtime.sockets, session,
                req.symbol, req.volume, req.pin, req.market_type,
            )
    except WebSocketConnectionClosedException:
        raise HTTPException(503, "Session closed — please re-login")

    with runtime.orders_lock:
        runtime.orders[ord_hash] = {
            "ord_hash":       ord_hash,
            "status":         "sent",
            "symbol":         req.symbol,
            "side":           req.side,
            "created_ts":     time.time(),
            "last_update_ts": time.time(),
        }

    # Give WebSocket callbacks a short window to report immediate exchange status
    # (especially rejection) so the API response is less misleading.
    deadline = time.time() + 2.0
    observed_status = "sent"
    while time.time() < deadline:
        with runtime.orders_lock:
            current         = runtime.orders.get(ord_hash, {})
            observed_status = current.get("status", observed_status)
            if observed_status in _TERMINAL_ORDER_STATES:
                break
        time.sleep(0.1)

    with runtime.orders_lock:
        latest = runtime.orders.get(ord_hash, {})

    if latest.get("status") == "rejected":
        return {
            "status":   "rejected",
            "ord_hash": ord_hash,
            "message":  "Order was rejected by exchange",
            "order":    latest,
        }

    return {
        "status":   latest.get("status", "sent"),
        "ord_hash": ord_hash,
        "note": (
            "Order submitted. For live updates check GET /orders. "
            "If still pending, use GET /orders/outstanding for cancellable IDs."
        ),
        "order": latest,
    }


@router.post("/order/cancel")
def cancel(req: CancelOrderRequest, session: SessionDep):
    """
    Cancel a queued order.
    Supply both exch_order_id (FIX tag 37) and house_order_id (FIX tag 41).
    Get these from GET /orders/outstanding.

    order_side accepts numeric ("1","2","G","Y","8") OR the human label
    from HOUSE_ORDER_SIDE ("BUY","SELL","LBUY","LSELL","SHORT SELL").
    """
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")

    side_num = CANCEL_SIDE.get(req.order_side.upper(), req.order_side)
    log.info(
        f"[CANCEL] symbol={req.symbol}  "
        f"exch_order_id={req.exch_order_id}  "
        f"house_order_id={req.house_order_id}  "
        f"side={req.order_side!r} → {side_num!r}"
    )

    try:
        ord_hash = cancel_order(
            runtime.sockets, session,
            req.symbol, req.exch_order_id, req.house_order_id,
            side_num, req.market_type, req.pin,
        )
    except WebSocketConnectionClosedException:
        raise HTTPException(503, "Session closed — please re-login")

    return {"status": "cancel_sent", "ord_hash": ord_hash}


@router.post("/order/cancel/by-exch")
def cancel_by_exch_id(req: CancelByExchIdRequest, session: SessionDep):
    """
    Convenience: cancel by EXCH_ORDER_ID only.
    Fetches outstanding orders from REST to resolve house_order_id automatically.
    """
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")

    outstanding = get_outstanding_orders(session)
    match = next(
        (o for o in outstanding if o.get("EXCH_ORDER_ID") == req.exch_order_id), None,
    )
    if not match:
        raise HTTPException(
            404,
            f"EXCH_ORDER_ID {req.exch_order_id!r} not found in outstanding orders. "
            "It may already be filled, cancelled, or the ID is incorrect.",
        )

    symbol         = match["SECURITY_SYMBOL"]
    house_order_id = match["HOUSE_ORDER_ID"]
    market_type    = match.get("MARKET_TYPE", MARKET_REG)
    h_side         = match.get("HOUSE_ORDER_SIDE", "BUY")
    side_num       = CANCEL_SIDE.get(h_side.upper(), match.get("ORDER_SIDE", "1"))

    log.info(
        f"[CANCEL/BY-EXCH] symbol={symbol}  "
        f"exch_order_id={req.exch_order_id}  "
        f"house_order_id={house_order_id}  "
        f"side={h_side!r} → {side_num!r}"
    )

    try:
        ord_hash = cancel_order(
            runtime.sockets, session,
            symbol, req.exch_order_id, house_order_id,
            side_num, market_type, req.pin,
        )
    except WebSocketConnectionClosedException:
        raise HTTPException(503, "Session closed — please re-login")

    return {
        "status":         "cancel_sent",
        "ord_hash":       ord_hash,
        "symbol":         symbol,
        "exch_order_id":  req.exch_order_id,
        "house_order_id": house_order_id,
    }


@router.post("/order/change")
def change_order_route(req: ChangeOrderRequest, session: SessionDep):
    """
    Amend price and/or volume of a queued order.
    Get exch_order_id from GET /orders/outstanding.
    """
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")

    effective_pin = req.pin.strip() or runtime.pin
    log.info(
        f"[CHANGE] exch_order_id={req.exch_order_id}  "
        f"new_price={req.new_price}  new_volume={req.new_volume}"
    )

    try:
        ord_hash = change_order(
            runtime.sockets, session,
            req.exch_order_id, req.new_price, req.new_volume, effective_pin,
        )
    except Exception as e:
        raise HTTPException(500, f"Change order failed: {e}") from e

    return {"status": "change_sent", "ord_hash": ord_hash, "pin_used": bool(effective_pin)}


@router.post("/order/place-slo")
def route_place_slo(req_body: SloOrderRequest, session: SessionDep):
    """Place a stop-loss order via the MF socket."""
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")
    effective_pin = req_body.pin.strip() or runtime.pin
    try:
        ord_hash = place_slo_order(
            runtime.sockets, session,
            symbol=req_body.symbol.upper(),
            side=req_body.side,
            h_order_side=req_body.h_order_side,
            volume=req_body.volume,
            trigger_price=req_body.trigger_price,
            stop_price=req_body.stop_price,
            order_type=req_body.order_type,
            market_type=req_body.market_type,
            time_in_force=req_body.time_in_force,
            pin=effective_pin,
        )
    except Exception as e:
        raise HTTPException(500, f"SLO order failed: {e}") from e
    return {"status": "slo_sent", "ord_hash": ord_hash}


@router.get("/orders")
def list_orders():
    """List all orders tracked this session via WebSocket (od + pm frames)."""
    with runtime.orders_lock:
        return {"orders": list(runtime.orders.values())}


@router.get("/orders/outstanding")
def list_outstanding(session: SessionDep, symbol: str = "", market_type: str = ""):
    """Live REST query — returns all queued (cancellable) orders right now."""
    orders = get_outstanding_orders(session, symbol=symbol, market_type=market_type)
    return {"count": len(orders), "orders": orders}


@router.get("/orders/tradelogs")
def list_trade_logs(session: SessionDep, symbol: str = "", market_type: str = ""):
    """Live REST query — returns today's actual buy/sell trade logs."""
    orders = get_trade_logs(session, symbol=symbol, market_type=market_type)
    return {"count": len(orders), "orders": orders}


@router.get("/orders/activitylogs")
def list_activity_logs(session: SessionDep, symbol: str = "", market_type: str = ""):
    """Live REST query — full activity logs: queued, cancelled, rejected, filled."""
    orders = get_activity_logs(session, symbol=symbol, market_type=market_type)
    return {"count": len(orders), "orders": orders}


@router.get("/orders/consolidated")
def consolidated_logs(session: SessionDep, symbol: str = "", market_type: str = ""):
    """Consolidated trade logs: net buy/sell/position per symbol across all days."""
    rows = get_consolidated_trade_logs(session, symbol=symbol, market_type=market_type)
    return {"count": len(rows), "orders": rows}

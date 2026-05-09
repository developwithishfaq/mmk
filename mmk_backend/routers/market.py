"""
market.py — Market data, feed subscriptions, watchlist, and symbol search.

Endpoints
---------
GET  /market/top-movers
GET  /market/exchange-state
GET  /market/indices
GET  /market/status
GET  /market/item/{symbol}
GET  /market/item/{symbol}/periodic
GET  /market/item/{symbol}/chart
GET  /market/feed
POST /market/subscribe/mbo
POST /market/unsubscribe/mbo
POST /market/subscribe/mbp
POST /market/unsubscribe/mbp
GET  /watchlist
GET  /watchlist/csv
GET  /symbols
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from mmk_api import (
    PERIODIC_DURATIONS,
    get_exchange_state,
    get_feed_by_watch_type,
    get_indices_summary,
    get_item_detail,
    get_item_periodic_data,
    get_market_status,
    get_symbol_chart,
    get_top_movers,
    subscribe_mbo,
    subscribe_mbp,
    unsubscribe_mbo,
    unsubscribe_mbp,
)
from mmk_backend.api.deps import SessionDep
from mmk_backend.runtime_state import runtime
from mmk_backend.schemas import MboMbpRequest
from mmk_backend.services import auth_service

router = APIRouter()


@router.get("/market/top-movers")
def top_movers(session: SessionDep, mode: int = 0):
    """Top movers by mode: 1=gainers 2=top-change 3=top-% 4=high-price 5=losers."""
    items = get_top_movers(session, mode=mode)
    return {"count": len(items), "mode": mode, "items": items}


@router.get("/market/exchange-state")
def exchange_state(session: SessionDep):
    """Live exchange state — summary (adv/dec/unc/total) + per-index data."""
    return get_exchange_state(session)


@router.get("/market/indices")
def indices_summary(session: SessionDep):
    """Indices summary: one row per market segment with volume, value, status."""
    rows = get_indices_summary(session)
    return {"count": len(rows), "indices": rows}


@router.get("/market/status")
def market_status(session: SessionDep):
    """Current market open/close status."""
    return get_market_status(session)


@router.get("/market/item/{symbol}")
def route_item_detail(symbol: str, session: SessionDep, mkt_type: str = "REG"):
    """Single symbol detail snapshot (live quote + fundamentals)."""
    return get_item_detail(session, symbol=symbol.upper(), mkt_type=mkt_type)


@router.get("/market/item/{symbol}/periodic")
def route_item_periodic(
    symbol: str,
    session: SessionDep,
    duration: str = "1MO",
    mkt_type: str = "REG",
):
    """Historical OHLC / volume stats. duration: 5DY 1MO 6MO YTD 1YR 5YR."""
    if duration not in PERIODIC_DURATIONS:
        raise HTTPException(400, f"duration must be one of {PERIODIC_DURATIONS}")
    return {
        "symbol":   symbol.upper(),
        "duration": duration,
        "rows":     get_item_periodic_data(session, symbol=symbol.upper(), duration=duration),
    }


@router.get("/market/item/{symbol}/chart")
def route_symbol_chart(
    symbol: str,
    session: SessionDep,
    mkt_type: str = "REG",
    index: str = "",
    mode: str = "1",
):
    """Price/volume chart series for a symbol (up to 100 intraday points)."""
    return {
        "symbol": symbol.upper(),
        "points": get_symbol_chart(
            session,
            symbol=symbol.upper(),
            mkt_type=mkt_type,
            index=index,
            mode=mode,
        ),
    }


@router.get("/market/feed")
def route_feed_by_watch_type(session: SessionDep, feed_type: str = "F", code: str = ""):
    """
    Live feed filtered by watch type.
    feed_type: S=sector  I=index  F=futures  U=upper-cap  L=lower-cap
    """
    return {
        "feed_type": feed_type,
        "code":      code,
        "items":     get_feed_by_watch_type(session, feed_type=feed_type, code=code),
    }


@router.post("/market/subscribe/mbo")
def sub_mbo(req: MboMbpRequest):
    """Subscribe to MBO (market-by-order) depth feed for a symbol."""
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")
    subscribe_mbo(runtime.sockets, req.symbol, req.market)
    return {"status": "subscribed", "feed": "mbo", "symbol": req.symbol}


@router.post("/market/unsubscribe/mbo")
def unsub_mbo(req: MboMbpRequest):
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")
    unsubscribe_mbo(runtime.sockets, req.symbol, req.market)
    return {"status": "unsubscribed", "feed": "mbo", "symbol": req.symbol}


@router.post("/market/subscribe/mbp")
def sub_mbp(req: MboMbpRequest):
    """Subscribe to MBP (market-by-price / order book depth) feed for a symbol."""
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")
    subscribe_mbp(runtime.sockets, req.symbol, req.market)
    return {"status": "subscribed", "feed": "mbp", "symbol": req.symbol}


@router.post("/market/unsubscribe/mbp")
def unsub_mbp(req: MboMbpRequest):
    if runtime.sockets is None:
        raise HTTPException(503, "Not connected")
    unsubscribe_mbp(runtime.sockets, req.symbol, req.market)
    return {"status": "unsubscribed", "feed": "mbp", "symbol": req.symbol}


@router.get("/watchlist")
def list_watchlist():
    """Return the latest watchlist prices as JSON for the browser UI."""
    with runtime.prices_lock:
        return {"count": len(runtime.prices), "items": list(runtime.prices.values())}


@router.get("/watchlist/csv")
def download_csv():
    """Download the latest watchlist prices CSV (auto-updated on every tick)."""
    if not os.path.exists(runtime.settings.csv_file):
        raise HTTPException(404, "CSV not ready yet — no ticks received.")
    return FileResponse(
        path=runtime.settings.csv_file,
        media_type="text/csv",
        filename="watchlist_prices.csv",
    )


@router.get("/symbols")
def list_symbols(req: Request, session: SessionDep, q: str = "", limit: int = 200):
    """Active symbol suggestions for UI autocomplete. Requires auth token."""
    if not auth_service.is_authenticated(req):
        raise HTTPException(401, "Not authenticated")
    query = q.strip().lower()
    with runtime.symbols_lock:
        symbols = list(runtime.symbols)
    if query:
        symbols = [
            s for s in symbols
            if query in s.get("symbol", "").lower() or query in s.get("name", "").lower()
        ]
    safe_limit = max(1, min(limit, 1000))
    return {"count": len(symbols[:safe_limit]), "items": symbols[:safe_limit]}

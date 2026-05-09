"""
daily_trader_hooks.py — broker adapter functions for the DailyTrader bot.

Responsibilities
----------------
- Owning the process-wide BrokerView singleton (lazy construction).
- Implementing every daily_trader.Hooks callback so the bot can fetch quotes,
  cash, and place / cancel orders without importing server.py.
- install_hooks() — wires the current session into the running bot instance.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import daily_trader
from broker_view import BrokerView
from mmk_api import (
    CANCEL_SIDE,
    MARKET_REG,
    cancel_order,
    get_feed_by_watch_type,
    place_limit_buy,
    place_limit_sell,
    place_market_sell,
    place_slo_order,
    subscribe_watchlist,
)
from mmk_backend.runtime_state import runtime
from websocket._exceptions import WebSocketConnectionClosedException

log = logging.getLogger("mmk.dt_hooks")


# ── BrokerView singleton ────────────────────────────────────────────────────

def get_broker_view() -> BrokerView:
    """Return process-wide BrokerView; constructed lazily on first call."""
    if runtime.broker_view is None:
        with runtime.broker_view_lock:
            if runtime.broker_view is None:
                runtime.broker_view = BrokerView(
                    session_provider=lambda: runtime.session,
                    pin_provider=lambda: runtime.pin,
                )
    return runtime.broker_view


# ── Hook implementations ────────────────────────────────────────────────────

def dt_get_quote(symbol: str) -> Optional[dict]:
    sym = (symbol or "").upper()
    with runtime.prices_lock:
        return runtime.prices.get(sym)


def dt_subscribe_symbols(symbols: list[str]) -> None:
    if runtime.sockets is None or runtime.session is None:
        return
    syms = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not syms:
        return
    try:
        subscribe_watchlist(runtime.sockets, syms)
    except Exception as e:
        log.warning(f"daily_trader subscribe failed: {e}")


def dt_fetch_feed(feed_type: str, code: str) -> list[dict]:
    if runtime.session is None:
        return []
    try:
        return get_feed_by_watch_type(runtime.session, feed_type=feed_type, code=code) or []
    except Exception as e:
        log.warning(f"daily_trader feed failed: {e}")
        return []


def dt_get_cash() -> float:
    """Cached cash via BrokerView (30 s TTL); avoids hitting REST every tick."""
    try:
        return float(get_broker_view().cash() or 0.0)
    except Exception as e:
        log.warning(f"daily_trader cash fetch failed: {e}")
        return 0.0


def dt_broker_has_any_today(symbol: str) -> bool:
    """
    Return True if placing a new entry on `symbol` should be blocked.

    Blocks when:
      - There is a live outstanding order (not yet terminal) on the symbol, OR
      - The activity log shows a filled / partial-fill order today.

    Does NOT block when the only history is rejected or cancelled orders —
    those had zero economic effect and a fresh attempt is perfectly valid.
    """
    try:
        return bool(get_broker_view().has_blocking_order_for(symbol))
    except Exception as e:
        log.warning(f"daily_trader has_blocking_order_for failed: {e}")
        return False


def dt_broker_market_open() -> bool:
    """
    Return True if the market is open for trading.

    Priority:
    1. Live socket `ht` message in runtime.market_status — instant, no REST call.
    2. BrokerView REST fallback (30s TTL) — used if socket hasn't received ht yet.
    """
    from mmk_backend.services.socket_handler import _OPEN_STATUSES  # noqa: PLC0415
    status = runtime.market_status
    if status != "UNKNOWN":
        return status in _OPEN_STATUSES
    # Fallback: poll REST if socket hasn't delivered ht yet
    try:
        return bool(get_broker_view().is_market_open())
    except Exception as e:
        log.warning(f"daily_trader broker_market_open failed: {e}")
        return False


def dt_get_broker_positions() -> list[dict]:
    try:
        return list(get_broker_view().open_positions())
    except Exception as e:
        log.warning(f"daily_trader get_broker_positions failed: {e}")
        return []


def dt_broker_refresh() -> dict:
    try:
        return get_broker_view().refresh() or {}
    except Exception as e:
        log.warning(f"daily_trader broker_refresh failed: {e}")
        return {}


def dt_track_order(ord_hash: str, symbol: str, side: str) -> None:
    """Register a daily-trader-placed order in the global runtime.orders cache."""
    with runtime.orders_lock:
        runtime.orders[ord_hash] = {
            "ord_hash":       ord_hash,
            "status":         "sent",
            "symbol":         symbol,
            "side":           side,
            "daily_trader":   True,
            "created_ts":     time.time(),
            "last_update_ts": time.time(),
        }


def _check_cap_limits(symbol: str, price: float, side: str) -> None:
    """
    Raise ValueError if `price` is outside the exchange circuit-breaker limits.
    Key format: "MARKET_SYMBOL" (e.g. "01_OGDC").  Uses MARKET_REG ("01").
    Silently skips if limits are not yet loaded for this symbol.
    """
    key = f"{MARKET_REG}_{symbol.upper()}"
    with runtime.cap_limits_lock:
        limits = runtime.cap_limits.get(key)
    if not limits:
        return  # limits not loaded yet — don't block the order
    upper = limits.get("upper", 0.0)
    lower = limits.get("lower", 0.0)
    if upper > 0 and price > upper:
        raise ValueError(
            f"Order price {price} exceeds upper circuit limit {upper} for {symbol}"
        )
    if lower > 0 and price < lower:
        raise ValueError(
            f"Order price {price} below lower circuit limit {lower} for {symbol}"
        )


def dt_place_limit_buy(symbol: str, price: float, qty: int) -> str:
    if runtime.sockets is None or runtime.session is None:
        raise WebSocketConnectionClosedException("not connected")
    _check_cap_limits(symbol, price, "buy")
    ord_hash = place_limit_buy(
        runtime.sockets, runtime.session, symbol, str(price), str(int(qty)), runtime.pin,
    )
    dt_track_order(ord_hash, symbol, "buy")
    return ord_hash


def dt_place_limit_sell(symbol: str, price: float, qty: int) -> str:
    if runtime.sockets is None or runtime.session is None:
        raise WebSocketConnectionClosedException("not connected")
    _check_cap_limits(symbol, price, "sell")
    ord_hash = place_limit_sell(
        runtime.sockets, runtime.session, symbol, str(price), str(int(qty)), runtime.pin,
    )
    dt_track_order(ord_hash, symbol, "sell")
    return ord_hash


def dt_place_market_sell(symbol: str, qty: int) -> str:
    if runtime.sockets is None or runtime.session is None:
        raise WebSocketConnectionClosedException("not connected")
    ord_hash = place_market_sell(
        runtime.sockets, runtime.session, symbol, str(int(qty)), runtime.pin,
    )
    dt_track_order(ord_hash, symbol, "sell")
    return ord_hash


def dt_cancel_order(ord_hash: str) -> bool:
    """Look up exch/house IDs from the runtime cache, then cancel."""
    if runtime.sockets is None or runtime.session is None:
        return False
    with runtime.orders_lock:
        o = dict(runtime.orders.get(ord_hash, {}))
    exch  = o.get("exch_order_id")  or ""
    house = o.get("house_order_id") or ""
    if not exch or not house:
        return False
    sym  = o.get("symbol") or ""
    side = o.get("side_num") or ("1" if o.get("side") == "buy" else "2")
    try:
        cancel_order(
            runtime.sockets, runtime.session,
            sym, exch, house, side, MARKET_REG, runtime.pin,
        )
        return True
    except Exception as e:
        log.warning(f"daily_trader cancel failed: {e}")
        return False


def dt_place_slo(symbol: str, qty: int, trigger_price: float, stop_price: float) -> str:
    if runtime.sockets is None or runtime.session is None:
        raise WebSocketConnectionClosedException("not connected")
    ord_hash = place_slo_order(
        runtime.sockets, runtime.session,
        symbol=symbol,
        side="2",
        h_order_side="SELL",
        volume=str(int(qty)),
        trigger_price=str(trigger_price),
        stop_price=str(stop_price),
        pin=runtime.pin,
    )
    dt_track_order(ord_hash, symbol, "sell")
    return ord_hash


# ── Hook installation ───────────────────────────────────────────────────────

def install_hooks() -> None:
    """Wire the current session into the daily trader (called after login)."""
    get_broker_view()   # pre-touch so the session_provider closure is ready
    hooks = daily_trader.Hooks(
        get_quote=dt_get_quote,
        subscribe_symbols=dt_subscribe_symbols,
        fetch_feed=dt_fetch_feed,
        get_cash=dt_get_cash,
        place_limit_buy=dt_place_limit_buy,
        place_limit_sell=dt_place_limit_sell,
        place_market_sell=dt_place_market_sell,
        cancel_order=dt_cancel_order,
        place_slo=dt_place_slo,
        broker_has_any_today=dt_broker_has_any_today,
        broker_market_open=dt_broker_market_open,
        get_broker_positions=dt_get_broker_positions,
        broker_refresh=dt_broker_refresh,
    )
    daily_trader.get().set_hooks(hooks)

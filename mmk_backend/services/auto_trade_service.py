"""
auto_trade_service.py — bridge between live market ticks and the auto-trade module.

Responsibilities
----------------
- Deciding whether the market is currently open (entry gate for strategies).
- Translating a TradeIntent into a broker WebSocket order (place_intent).
- Hot-path tick handler: evaluate all enabled strategies for a symbol and
  dispatch any resulting intents to the broker (run_auto_trade_for_symbol).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import auto_trade
from mmk_api import (
    place_limit_buy,
    place_limit_sell,
    place_market_buy,
    place_market_sell,
)
from mmk_backend.runtime_state import runtime
from websocket._exceptions import WebSocketConnectionClosedException

log = logging.getLogger("mmk.auto_trade_svc")


def is_market_open() -> bool:
    if runtime.session is None:
        return False
    return str(getattr(runtime.session, "mkt_stat", "")).upper() == "OPEN"


def place_intent(intent: "auto_trade.TradeIntent") -> Optional[str]:
    """Submit a TradeIntent through the broker socket. Returns ord_hash."""
    if runtime.sockets is None or runtime.session is None:
        raise WebSocketConnectionClosedException("not connected")
    qty = str(int(intent.qty))
    if intent.side == "buy":
        if intent.order_type == "limit" and intent.price:
            return place_limit_buy(
                runtime.sockets, runtime.session, intent.symbol,
                str(round(float(intent.price), 4)), qty, intent.pin,
            )
        return place_market_buy(
            runtime.sockets, runtime.session, intent.symbol, qty, intent.pin,
        )
    # sell side: always market for safety on exits
    return place_market_sell(
        runtime.sockets, runtime.session, intent.symbol, qty, intent.pin,
    )


def run_auto_trade_for_symbol(symbol: str, tick: dict) -> None:
    """Evaluate enabled strategies for `symbol` and place any resulting orders. Hot path."""
    if runtime.sockets is None or runtime.session is None:
        return
    try:
        intents = auto_trade.evaluate_for_symbol(
            symbol, tick,
            market_open=is_market_open(),
            fallback_pin=runtime.pin,
        )
    except Exception as e:
        log.warning(f"[AUTO-TRADE] evaluate error for {symbol}: {e}")
        return

    for intent in intents:
        if intent.dry_run:
            continue
        try:
            ord_hash = place_intent(intent)
        except WebSocketConnectionClosedException:
            auto_trade.rollback_intent(intent, "session closed")
            return
        except Exception as e:
            auto_trade.rollback_intent(intent, f"placement error: {e}")
            log.warning(f"[AUTO-TRADE] placement error for {intent.symbol}: {e}")
            continue
        if not ord_hash:
            auto_trade.rollback_intent(intent, "no ord_hash returned")
            continue
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
        auto_trade.commit_intent_placed(intent, ord_hash)
        log.info(
            f"[AUTO-TRADE] {intent.symbol} {intent.side.upper()} qty={intent.qty} "
            f"ord={ord_hash} strategy={intent.strategy_id}"
        )

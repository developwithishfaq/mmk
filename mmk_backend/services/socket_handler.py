"""
socket_handler.py — WebSocket message dispatcher for the broker connection.

Responsibilities
----------------
- Parsing every frame from the PM (order placement) and MF (market feed) sockets.
- Routing FIX execution reports (pm frames) to the runtime order cache,
  auto-trade reconciliation, and the daily-trader on_order_update callback.
- Updating the live price cache and writing the watchlist CSV on every tick.
- Delegating auto-trade evaluation to auto_trade_service.run_auto_trade_for_symbol.
"""

from __future__ import annotations

import csv
import logging
import time

import auto_trade
import daily_trader
from mmk_backend.fix_parser import parse_fix
from mmk_backend.runtime_state import runtime
from mmk_backend.services.auto_trade_service import run_auto_trade_for_symbol

log = logging.getLogger("mmk.socket")

CSV_FIELDS = [
    "symbol", "market", "last", "bid", "ask",
    "high", "low", "change", "volume", "prev_close",
]


# ── Internal helpers ────────────────────────────────────────────────────────

def _write_csv() -> None:
    with runtime.prices_lock:
        rows = list(runtime.prices.values())
    with open(runtime.settings.csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _parse_price_row(row: list) -> dict | None:
    if len(row) < 17:
        return None
    return {
        "symbol":     str(row[0]),
        "market":     str(row[1]),
        "last":       str(row[8]),
        "bid":        str(row[5]),
        "ask":        str(row[6]),
        "high":       str(row[14]),
        "low":        str(row[15]),
        "change":     str(row[16]),
        "volume":     str(row[17]) if len(row) > 17 else "",
        "prev_close": str(row[11]),
    }


# ── Public dispatcher ───────────────────────────────────────────────────────

def on_message(name: str, raw: str, parsed: dict) -> None:  # noqa: C901
    # Stamp every frame so the watchdog can detect a dead connection.
    runtime.last_message_ts = time.time()

    t = parsed.get("t")

    # ── od: order delivered to exchange ────────────────────────────────────
    if t == "od":
        ord_hash = parsed.get("d", "")
        log.info(f"[OD] Order delivered  ordHash={ord_hash}")
        with runtime.orders_lock:
            if ord_hash not in runtime.orders:
                runtime.orders[ord_hash] = {}
            runtime.orders[ord_hash].update({
                "ord_hash":       ord_hash,
                "status":         "delivered",
                "last_update_ts": time.time(),
            })

    # ── pm: FIX execution report ────────────────────────────────────────────
    elif t == "pm":
        fix_str = parsed.get("d", "")
        fix = parse_fix(fix_str)

        exch_order_id  = fix.get("37", "")   # FIX tag 37 → use with cancel_order()
        house_order_id = fix.get("41", "")   # FIX tag 41 → use with cancel_order()
        client_ord_id  = fix.get("11", "")   # our md5 ordHash alias
        symbol         = fix.get("55", "")
        side_num       = fix.get("54", "")
        side_label     = "buy" if side_num == "1" else "sell"
        status_map     = {
            "0": "new", "1": "partial_fill", "2": "filled",
            "4": "cancelled", "8": "rejected",
        }
        status = status_map.get(fix.get("39", ""), f"unknown({fix.get('39', '')})")

        log.info(
            f"[PM] FIX report  symbol={symbol}  side={side_label}  "
            f"exch_order_id={exch_order_id}  house_order_id={house_order_id}  "
            f"client_order_id={client_ord_id}  status={status}"
        )
        log.info(
            f"[PM] To cancel → exch_order_id={exch_order_id!r}  "
            f"house_order_id={house_order_id!r}"
        )

        with runtime.orders_lock:
            # Primary: match by client_order_id (== ordHash).
            matched_hash = None
            for h, o in runtime.orders.items():
                if o.get("client_order_id") == client_ord_id or h == client_ord_id:
                    matched_hash = h
                    break

            # Fallback: most-recent pending order by symbol + side.
            # IMPORTANT: never grab a daily-trader order this way — strict match only.
            if not matched_hash and symbol and side_label:
                candidates = []
                for h, o in runtime.orders.items():
                    if o.get("daily_trader"):
                        continue
                    if (
                        o.get("symbol") == symbol
                        and o.get("side") == side_label
                        and o.get("status") in {"sent", "delivered", "new"}
                    ):
                        candidates.append((o.get("created_ts", 0.0), h))
                if candidates:
                    candidates.sort(reverse=True)
                    matched_hash = candidates[0][1]

            key   = matched_hash or exch_order_id or client_ord_id
            entry = runtime.orders.get(key, {})
            entry.update({
                "ord_hash":        key,
                "symbol":          symbol,
                "side":            side_label,
                "side_num":        side_num,
                "exch_order_id":   exch_order_id,
                "house_order_id":  house_order_id,
                "client_order_id": client_ord_id,
                "status":          status,
                "last_update_ts":  time.time(),
            })
            runtime.orders[key] = entry
            auto_sid = entry.get("auto_strategy_id")

        # Reconcile auto-trade manager state.
        if auto_sid and status in ("rejected", "cancelled", "filled", "partial_fill"):
            for candidate in (matched_hash, key, client_ord_id):
                if candidate:
                    auto_trade.reconcile_pm_status(candidate, status)

        # Reconcile daily trader — STRICT match only.
        if status in ("rejected", "cancelled", "filled", "partial_fill"):
            try:
                fill_price = float(fix.get("31") or 0.0)
                fill_qty   = int(float(fix.get("32") or 0))
                cum_qty    = int(float(fix.get("14") or 0))
                avg_px     = float(fix.get("6")  or 0.0)
            except (TypeError, ValueError):
                fill_price, fill_qty, cum_qty, avg_px = 0.0, 0, 0, 0.0

            bot  = daily_trader.get()
            seen: set[str] = set()
            for candidate in (matched_hash, key, client_ord_id):
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                if candidate not in bot._ord_to_pos:
                    continue
                try:
                    bot.on_order_update(
                        candidate, status,
                        fill_price=fill_price,
                        fill_qty=fill_qty,
                        cum_qty=cum_qty,
                        avg_px=avg_px,
                        symbol=symbol,
                    )
                except Exception as e:
                    log.warning(f"daily_trader reconcile failed: {e}")
                break

    # ── or: order accept / reject from broker server ────────────────────────
    elif t == "or":
        d        = parsed.get("d", {})
        ord_hash = d.get("ordHash", "")
        if d.get("success"):
            log.info(f"[OR] Order accepted  ordHash={ord_hash}")
        else:
            log.warning(f"[OR] Order rejected  ordHash={ord_hash}  msg={d.get('msg')}")

    # ── mr: market reject ───────────────────────────────────────────────────
    elif t == "mr":
        d = parsed.get("d", {})
        log.warning(
            f"[MR] Market reject  "
            f"HOUSE_ORDER_ID={d.get('HOUSE_ORDER_ID')}  "
            f"message={d.get('message')}"
        )

    # ── cr: cancel acknowledgement ──────────────────────────────────────────
    elif t == "cr":
        log.info(f"[CR] Cancel ack  {parsed.get('d')}")

    # ── co: change order acknowledgement ───────────────────────────────────
    elif t == "co":
        log.info(f"[CO] Change order ack  {parsed.get('d', {})}")

    # ── chgr: change order rejection ────────────────────────────────────────
    elif t == "chgr":
        d = parsed.get("d", {})
        log.warning(
            f"[CHGR] Change order rejected  "
            f"symbol={d.get('SECURITY_SYMBOL')}  "
            f"error={d.get('ERROR_CODE')}  msg={d.get('msg')}"
        )

    elif t in ("mo", "mp"):
        pass  # depth data — ignored at server level

    elif t == "ds":
        log.warning(f"[DS] Session displaced: {parsed.get('d', '')}")

    elif t == "ig":
        log.info(f"[IG] {parsed.get('d')}")
    elif t == "st":
        log.info(f"[ST] Server time  {parsed.get('d', {}).get('dateTime')}")
    elif t == "hm":
        log.info(f"[HM] Handshake  {parsed.get('d')}")
    elif t in ("hb", "hf", "es", "tv"):
        pass  # suppressed heartbeat / feed noise

    # ── mf / fma: price ticks ──────────────────────────────────────────────
    elif t in ("mf", "fma"):
        d    = parsed.get("d", [])
        rows = d if (d and isinstance(d[0], list)) else [d]
        updated = False
        touched: list[str] = []
        with runtime.prices_lock:
            for row in rows:
                price = _parse_price_row(row)
                if price:
                    runtime.prices[price["symbol"]] = price
                    touched.append(price["symbol"])
                    updated = True
        if updated:
            _write_csv()
            bot = daily_trader.get()
            for sym in touched:
                tick_copy = dict(runtime.prices.get(sym, {}))
                if not tick_copy:
                    continue
                # Auto-trade strategies (grid / rule-based)
                run_auto_trade_for_symbol(sym, tick_copy)
                # Daily trader: VWAP accumulation + tick-driven entry evaluation.
                # on_tick() is lightweight when the bot is STOPPED or HALTED —
                # it only updates the VWAP accumulator and returns immediately.
                try:
                    bot.on_tick(sym, tick_copy)
                except Exception as _e:
                    log.warning(f"daily_trader on_tick failed for {sym}: {_e}")

    else:
        log.info(f"[SOCKET/{name}] t={t!r}  raw={raw[:300]}")

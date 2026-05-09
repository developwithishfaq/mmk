"""
auto_trade.py — Persistent auto-trading helpers.

Design summary (after bugfix pass):
- The store (`auto_trade_state.json`) is loaded once and held in memory.
- Manager objects (PSXPositionManager) are the runtime source of truth.
- The store row mirrors the manager's runtime; it is persisted by a background
  save thread debounced to `SAVE_DEBOUNCE_SEC` to keep tick handling fast.
- Order placements are tracked in `_pending_orders` so that broker rejections
  arriving later via FIX `pm` frames can roll back manager state precisely.
- All public mutations go through helpers; the websocket thread never touches
  disk or strategy lists directly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from copy import deepcopy
from typing import Any, Callable, Optional

from psx_position_manager import PSXPositionManager, manager_from_strategy_row

log = logging.getLogger("mmk.auto")

AUTO_TRADE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "auto_trade_state.json"
)
SAVE_DEBOUNCE_SEC = 1.0
EVENT_LOG_LIMIT = 80
TRADE_HISTORY_LIMIT = 120
PENDING_ORDER_TTL_SEC = 600
DEFAULT_EXIT_COOLDOWN_SEC = 10.0
DEFAULT_WARMUP_TICKS = 10
MIN_STOP_PCT = 0.005

# ──────────────────────────────────────────────────────────────────
# Global state
# ──────────────────────────────────────────────────────────────────

_lock = threading.RLock()
_store: dict[str, Any] = {"strategies": [], "version": 2}
_managers: dict[str, PSXPositionManager] = {}

_last_eval_ts: dict[str, float] = {}
_last_placed_ts: dict[str, float] = {}
_last_exit_ts: dict[str, float] = {}
_warmup_counts: dict[str, int] = {}

# ord_hash → {strategy_id, snapshot, action, ts}
_pending_orders: dict[str, dict] = {}

_dirty = False
_dirty_event = threading.Event()
_save_thread: Optional[threading.Thread] = None
_save_thread_stop = threading.Event()


# ──────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────

def _atomic_write(data: dict) -> None:
    tmp = AUTO_TRADE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, AUTO_TRADE_FILE)


def _read_disk() -> dict:
    if not os.path.exists(AUTO_TRADE_FILE):
        return {"strategies": [], "version": 2}
    try:
        with open(AUTO_TRADE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"strategies": [], "version": 2}
        data.setdefault("strategies", [])
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"auto_trade: failed to load store ({e}); starting empty")
        return {"strategies": [], "version": 2}


def _mark_dirty() -> None:
    global _dirty
    _dirty = True
    _dirty_event.set()


def _flush() -> None:
    global _dirty
    with _lock:
        if not _dirty:
            return
        snapshot = deepcopy(_store)
        _dirty = False
        _dirty_event.clear()
    try:
        _atomic_write(snapshot)
    except OSError as e:
        log.warning(f"auto_trade: save failed ({e})")
        with _lock:
            _dirty = True
            _dirty_event.set()


def _save_loop() -> None:
    while not _save_thread_stop.is_set():
        if _dirty_event.wait(timeout=SAVE_DEBOUNCE_SEC):
            try:
                _flush()
            except Exception as e:  # noqa: BLE001
                log.warning(f"auto_trade saver loop error: {e}")


def _start_save_thread() -> None:
    global _save_thread
    if _save_thread and _save_thread.is_alive():
        return
    _save_thread_stop.clear()
    _save_thread = threading.Thread(
        target=_save_loop, name="auto-trade-saver", daemon=True
    )
    _save_thread.start()


def init() -> None:
    """Load store and rebuild managers. Call once at server startup."""
    global _store, _managers
    with _lock:
        _store = _read_disk()
        new_map: dict[str, PSXPositionManager] = {}
        normalized = []
        for row in _store.get("strategies", []):
            if not isinstance(row, dict):
                continue
            sid = row.get("id") or str(uuid.uuid4())
            row["id"] = sid
            row.setdefault("event_log", [])
            row.setdefault("trade_history", [])
            row.setdefault("dry_run", False)
            row.setdefault("min_tick_interval_sec", 0.0)
            row.setdefault("exit_cooldown_sec", DEFAULT_EXIT_COOLDOWN_SEC)
            row.setdefault("warmup_ticks", DEFAULT_WARMUP_TICKS)
            row.setdefault("order_type", "market")
            row.setdefault("max_pyramids", 2)
            row.setdefault("pin", "")
            row.setdefault("last_signal", None)
            row.setdefault("last_action", None)
            row.setdefault("last_action_ts", None)
            row.setdefault("last_tick_ts", None)
            row.setdefault("symbol", str(row.get("symbol", "")).strip().upper())
            new_map[sid] = manager_from_strategy_row(row)
            normalized.append(row)
        _store["strategies"] = normalized
        _managers = new_map
    _start_save_thread()
    log.info(f"auto_trade: loaded {len(_managers)} strategies from {AUTO_TRADE_FILE}")


def shutdown() -> None:
    """Flush pending writes."""
    _save_thread_stop.set()
    _dirty_event.set()
    _flush()


# ──────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────

def _row_for(strategy_id: str) -> Optional[dict]:
    for r in _store.get("strategies", []):
        if r.get("id") == strategy_id:
            return r
    return None


def _append_event(row: dict, message: str, detail: Optional[dict] = None) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    entry: dict[str, Any] = {"ts": ts, "message": message}
    if detail:
        entry["detail"] = detail
    log_lines = row.setdefault("event_log", [])
    log_lines.append(entry)
    row["event_log"] = log_lines[-EVENT_LOG_LIMIT:]


def _append_trade(
    row: dict,
    *,
    side: str,
    qty: int,
    price: Optional[float],
    status: str,
    action: Optional[dict] = None,
    ord_hash: Optional[str] = None,
    note: str = "",
) -> str:
    trade_id = str(uuid.uuid4())
    entry: dict[str, Any] = {
        "id": trade_id,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "side": side.lower(),
        "qty": int(qty),
        "price": float(price) if price is not None else None,
        "status": status,
        "ord_hash": ord_hash or "",
        "note": note,
    }
    if action:
        entry["action"] = action
    history = row.setdefault("trade_history", [])
    history.append(entry)
    row["trade_history"] = history[-TRADE_HISTORY_LIMIT:]
    return trade_id


def _update_trade_status(row: dict, trade_id: str, status: str, note: str = "") -> None:
    history = row.get("trade_history", [])
    for entry in reversed(history):
        if entry.get("id") == trade_id:
            entry["status"] = status
            if note:
                entry["note"] = note
            break


def _mirror_runtime(row: dict) -> None:
    m = _managers.get(row.get("id"))
    if m is None:
        return
    row["runtime"] = m.snapshot_runtime_state()


def _public_view(row: dict) -> dict:
    m = _managers.get(row.get("id"))
    runtime = m.snapshot_runtime_state() if m else (row.get("runtime") or {})
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol", ""),
        "enabled": row.get("enabled", False),
        "dry_run": row.get("dry_run", False),
        "account_balance": row.get("account_balance", 0),
        "risk_per_trade": row.get("risk_per_trade", 0.02),
        "max_pyramids": row.get("max_pyramids", 2),
        "order_type": row.get("order_type", "market"),
        "min_tick_interval_sec": float(row.get("min_tick_interval_sec", 0.0)),
        "exit_cooldown_sec": float(row.get("exit_cooldown_sec", DEFAULT_EXIT_COOLDOWN_SEC)),
        "warmup_ticks": int(row.get("warmup_ticks", DEFAULT_WARMUP_TICKS)),
        "runtime": runtime,
        "event_log": (row.get("event_log") or [])[-40:],
        "trade_history": (row.get("trade_history") or [])[-40:],
        "last_signal": row.get("last_signal"),
        "last_action": row.get("last_action"),
        "last_action_ts": row.get("last_action_ts"),
        "last_tick_ts": row.get("last_tick_ts"),
    }


def _drop_strategy_caches(strategy_id: str) -> None:
    _last_eval_ts.pop(strategy_id, None)
    _last_placed_ts.pop(strategy_id, None)
    _last_exit_ts.pop(strategy_id, None)
    _warmup_counts.pop(strategy_id, None)


# ──────────────────────────────────────────────────────────────────
# Public API: CRUD
# ──────────────────────────────────────────────────────────────────

def list_strategies() -> list[dict]:
    with _lock:
        return [_public_view(r) for r in _store.get("strategies", [])]


def get_strategy(strategy_id: str) -> Optional[dict]:
    with _lock:
        row = _row_for(strategy_id)
        return _public_view(row) if row else None


def add_strategy(payload: dict) -> dict:
    sym = str(payload.get("symbol", "")).strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    sid = str(uuid.uuid4())
    row: dict[str, Any] = {
        "id": sid,
        "symbol": sym,
        "enabled": bool(payload.get("enabled", False)),
        "dry_run": bool(payload.get("dry_run", False)),
        "account_balance": float(payload.get("account_balance", 1_000.0) or 0.0),
        "risk_per_trade": float(payload.get("risk_per_trade", 0.02) or 0.0),
        "max_pyramids": int(payload.get("max_pyramids", 2)),
        "order_type": payload.get("order_type", "market"),
        "min_tick_interval_sec": float(payload.get("min_tick_interval_sec", 0.0)),
        "exit_cooldown_sec": float(payload.get("exit_cooldown_sec", DEFAULT_EXIT_COOLDOWN_SEC)),
        "warmup_ticks": int(payload.get("warmup_ticks", DEFAULT_WARMUP_TICKS)),
        "pin": str(payload.get("pin", "")).strip(),
        "event_log": [],
        "trade_history": [],
        "last_signal": None,
        "last_action": None,
        "last_action_ts": None,
        "last_tick_ts": None,
        "runtime": {
            "state": "FLAT",
            "entries": [],
            "avg_entry_price": 0,
            "total_qty": 0,
            "pyramid_count": 0,
            "stop_loss_price": None,
            "initial_entry_price": None,
        },
    }
    with _lock:
        _store.setdefault("strategies", []).append(row)
        _managers[sid] = manager_from_strategy_row(row)
        _append_event(row, "Strategy created")
        _mark_dirty()
        return _public_view(row)


def patch_strategy(strategy_id: str, patch: dict) -> dict:
    """
    Mutates row + manager configuration in place. Does not change runtime
    position state (use reset_strategy for that).
    """
    with _lock:
        row = _row_for(strategy_id)
        if not row:
            raise KeyError(strategy_id)

        if "enabled" in patch and patch["enabled"] is not None:
            row["enabled"] = bool(patch["enabled"])
        if "dry_run" in patch and patch["dry_run"] is not None:
            row["dry_run"] = bool(patch["dry_run"])
        if "account_balance" in patch and patch["account_balance"] is not None:
            row["account_balance"] = float(patch["account_balance"])
        if "risk_per_trade" in patch and patch["risk_per_trade"] is not None:
            row["risk_per_trade"] = float(patch["risk_per_trade"])
        if "order_type" in patch and patch["order_type"] is not None:
            row["order_type"] = patch["order_type"]
        if "min_tick_interval_sec" in patch and patch["min_tick_interval_sec"] is not None:
            row["min_tick_interval_sec"] = float(patch["min_tick_interval_sec"])
        if "exit_cooldown_sec" in patch and patch["exit_cooldown_sec"] is not None:
            row["exit_cooldown_sec"] = float(patch["exit_cooldown_sec"])
        if "warmup_ticks" in patch and patch["warmup_ticks"] is not None:
            row["warmup_ticks"] = int(patch["warmup_ticks"])
        if "max_pyramids" in patch and patch["max_pyramids"] is not None:
            row["max_pyramids"] = int(patch["max_pyramids"])
        if "pin" in patch and patch["pin"] is not None:
            row["pin"] = str(patch["pin"]).strip()

        m = _managers.get(strategy_id)
        if m is not None:
            m.balance = float(row["account_balance"])
            m.risk_pct = float(row["risk_per_trade"])
            m.max_pyramids = int(row["max_pyramids"])

        _append_event(row, "Strategy updated", {k: v for k, v in patch.items() if v is not None})
        _mark_dirty()
        return _public_view(row)


def delete_strategy(strategy_id: str) -> bool:
    with _lock:
        before = len(_store.get("strategies", []))
        _store["strategies"] = [
            r for r in _store.get("strategies", []) if r.get("id") != strategy_id
        ]
        _managers.pop(strategy_id, None)
        _drop_strategy_caches(strategy_id)
        # Drop pending orders for this strategy
        for oh, p in list(_pending_orders.items()):
            if p.get("strategy_id") == strategy_id:
                _pending_orders.pop(oh, None)
        if len(_store["strategies"]) != before:
            _mark_dirty()
            return True
        return False


def reset_strategy(strategy_id: str) -> Optional[dict]:
    with _lock:
        row = _row_for(strategy_id)
        if not row:
            return None
        m = manager_from_strategy_row(
            {
                "symbol": row["symbol"],
                "account_balance": row["account_balance"],
                "risk_per_trade": row["risk_per_trade"],
                "max_pyramids": row["max_pyramids"],
                "runtime": {
                    "state": "FLAT",
                    "entries": [],
                    "avg_entry_price": 0,
                    "total_qty": 0,
                    "pyramid_count": 0,
                    "stop_loss_price": None,
                    "initial_entry_price": None,
                },
            }
        )
        _managers[strategy_id] = m
        _mirror_runtime(row)
        _drop_strategy_caches(strategy_id)
        _append_event(row, "Strategy reset to FLAT")
        _mark_dirty()
        return _public_view(row)


def collect_auto_symbols() -> list[str]:
    with _lock:
        return sorted(
            {
                str(r.get("symbol", "")).strip().upper()
                for r in _store.get("strategies", [])
                if r.get("enabled") and str(r.get("symbol", "")).strip()
            }
        )


# ──────────────────────────────────────────────────────────────────
# Runtime: tick parsing and signal computation
# ──────────────────────────────────────────────────────────────────

def parse_tick_numeric(tick: dict[str, Any]) -> Optional[dict]:
    try:
        last = float(str(tick.get("last", "0")).replace(",", ""))
        low = float(str(tick.get("low", "0")).replace(",", ""))
        high = float(str(tick.get("high", "0")).replace(",", ""))
        prev_close = float(str(tick.get("prev_close", "0")).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if last <= 0 or low <= 0 or high <= 0:
        return None
    if high < low:
        low, high = high, low
    return {"last": last, "low": low, "high": high, "prev_close": prev_close}


def compute_signal(state: str, tick_num: dict) -> Optional[str]:
    """Placeholder: simple long-only momentum vs prev_close."""
    last = tick_num["last"]
    pc = tick_num["prev_close"]
    if pc <= 0:
        return None
    if state == "FLAT" and last > pc:
        return "BUY"
    if state == "LONG" and last < pc:
        return "SELL"
    return None


# ──────────────────────────────────────────────────────────────────
# Runtime: per-tick evaluation
# ──────────────────────────────────────────────────────────────────

def _enforce_min_stop(entry: float, stop: float) -> float:
    """Ensure stop is at least MIN_STOP_PCT below entry (long side)."""
    min_stop = entry * (1.0 - MIN_STOP_PCT)
    return min(stop, min_stop)


class TradeIntent:
    __slots__ = ("strategy_id", "symbol", "side", "qty", "price", "order_type",
                 "pin", "snapshot", "action", "dry_run")

    def __init__(self, strategy_id: str, symbol: str, side: str, qty: int,
                 price: Optional[float], order_type: str, pin: str,
                 snapshot: dict, action: dict, dry_run: bool):
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.side = side  # "buy" or "sell"
        self.qty = qty
        self.price = price
        self.order_type = order_type
        self.pin = pin
        self.snapshot = snapshot
        self.action = action
        self.dry_run = dry_run


def evaluate_for_symbol(
    symbol: str,
    tick: dict,
    *,
    market_open: bool,
    fallback_pin: str,
) -> list[TradeIntent]:
    """
    Run all enabled strategies for `symbol` against the given tick.
    Returns intents that the caller (server) should place via the broker socket.
    Updates manager runtime state and the persistent row in-place.
    """
    sym_u = symbol.strip().upper()
    intents: list[TradeIntent] = []
    now = time.time()

    with _lock:
        rows = [r for r in _store.get("strategies", [])
                if r.get("enabled") and str(r.get("symbol", "")).strip().upper() == sym_u]
        if not rows:
            return intents

        for row in rows:
            sid = row["id"]
            row["last_tick_ts"] = now

            tick_num = parse_tick_numeric(tick)
            if not tick_num:
                # Bad/empty tick — don't waste throttle window or spam events.
                continue

            # ── Warmup: collect a few ticks before generating signals ──
            warmup_needed = int(row.get("warmup_ticks", DEFAULT_WARMUP_TICKS))
            seen = _warmup_counts.get(sid, 0) + 1
            _warmup_counts[sid] = seen
            if seen < warmup_needed:
                _last_eval_ts[sid] = now
                continue

            m = _managers.get(sid)
            if m is None:
                continue

            # ── Cooldown after a recent exit (avoid whipsaw) ───────────
            cooldown = float(row.get("exit_cooldown_sec", DEFAULT_EXIT_COOLDOWN_SEC))
            if m.state == "FLAT" and (now - _last_exit_ts.get(sid, 0.0)) < cooldown:
                _last_eval_ts[sid] = now
                continue

            # ── Optional: only act when market is open ─────────────────
            if not market_open:
                _last_eval_ts[sid] = now
                continue

            snapshot = m.snapshot_runtime_state()

            sig = compute_signal(m.state, tick_num)
            row["last_signal"] = sig

            # Apply a minimum stop floor for safety: monkey-patch by
            # peeking the result, then clamping inside the stored stop.
            action = m.on_signal(sig, tick_num)
            if (action and action.get("action") == "BUY"
                    and m.state == "LONG" and m.stop_loss_price is not None
                    and m.initial_entry_price):
                clamped = _enforce_min_stop(
                    float(m.initial_entry_price), float(m.stop_loss_price)
                )
                if clamped != m.stop_loss_price:
                    m.stop_loss_price = clamped
                    if "stop" in action:
                        action["stop"] = round(clamped, 2)

            _last_eval_ts[sid] = now

            if not action:
                continue

            act = action.get("action")
            row["last_action"] = act
            row["last_action_ts"] = now

            if act == "IGNORE":
                _append_event(row, action.get("reason", "IGNORE"), action)
                _mirror_runtime(row)
                _mark_dirty()
                continue

            # Per-strategy placement rate-limit
            if now - _last_placed_ts.get(sid, 0.0) < 1.5:
                m.restore_runtime_state(snapshot)
                _append_event(row, "Throttled: <1.5s since last placement", action)
                _mirror_runtime(row)
                _mark_dirty()
                continue

            # Build TradeIntent
            pin = (row.get("pin") or "").strip() or fallback_pin
            order_type = row.get("order_type", "market")
            dry = bool(row.get("dry_run", False))

            if act in ("BUY", "PYRAMID BUY"):
                qty = max(1, int(action.get("qty") or action.get("add_qty") or 0))
                price = float(action.get("price")) if action.get("price") is not None else None
                intent = TradeIntent(
                    sid, sym_u, "buy", qty, price, order_type, pin,
                    snapshot, action, dry,
                )
            elif act == "SELL (EXIT)":
                qty = max(1, int(action.get("qty") or 0))
                price = float(action.get("price")) if action.get("price") is not None else None
                intent = TradeIntent(
                    sid, sym_u, "sell", qty, price, "market", pin,
                    snapshot, action, dry,
                )
                _last_exit_ts[sid] = now
            else:
                _append_event(row, f"Unhandled action: {act}", action)
                _mirror_runtime(row)
                _mark_dirty()
                continue

            _mirror_runtime(row)
            if dry:
                _append_event(row, f"DRY-RUN {act}", action)
            else:
                _append_event(row, f"Decision: {act}", action)
            _mark_dirty()
            intents.append(intent)

        return intents


# ──────────────────────────────────────────────────────────────────
# Runtime: order ack / rejection reconciliation
# ──────────────────────────────────────────────────────────────────

def commit_intent_placed(intent: TradeIntent, ord_hash: str) -> None:
    with _lock:
        _last_placed_ts[intent.strategy_id] = time.time()
        row = _row_for(intent.strategy_id)
        trade_id = ""
        if row:
            trade_id = _append_trade(
                row,
                side=intent.side,
                qty=intent.qty,
                price=intent.price,
                status="sent",
                action=intent.action,
                ord_hash=ord_hash,
                note="Order sent to broker",
            )
        _pending_orders[ord_hash] = {
            "strategy_id": intent.strategy_id,
            "snapshot": intent.snapshot,
            "action": intent.action,
            "trade_id": trade_id,
            "ts": time.time(),
        }
        if row:
            _append_event(row, f"Order sent ({intent.side} {intent.qty}) ord={ord_hash}",
                          {"action": intent.action, "ord_hash": ord_hash})
            _mark_dirty()


def rollback_intent(intent: TradeIntent, reason: str) -> None:
    with _lock:
        m = _managers.get(intent.strategy_id)
        if m is not None:
            m.restore_runtime_state(intent.snapshot)
        row = _row_for(intent.strategy_id)
        if row:
            _mirror_runtime(row)
            _append_event(row, f"Rollback: {reason}", {"action": intent.action})
            _append_trade(
                row,
                side=intent.side,
                qty=intent.qty,
                price=intent.price,
                status="rolled_back",
                action=intent.action,
                note=reason,
            )
            _mark_dirty()


def reconcile_pm_status(client_ord_id: str, status: str) -> None:
    """Called by server's pm-frame handler. Rolls back manager on rejection."""
    if not client_ord_id:
        return
    with _lock:
        # Sweep stale pending entries (TTL safety net)
        cutoff = time.time() - PENDING_ORDER_TTL_SEC
        stale = [oh for oh, p in _pending_orders.items() if p.get("ts", 0) < cutoff]
        for oh in stale:
            _pending_orders.pop(oh, None)

        entry = _pending_orders.get(client_ord_id)
        if not entry:
            return
        sid = entry["strategy_id"]
        row = _row_for(sid)
        trade_id = str(entry.get("trade_id", "") or "")
        if status == "rejected":
            m = _managers.get(sid)
            if m is not None:
                m.restore_runtime_state(entry["snapshot"])
            if row:
                if trade_id:
                    _update_trade_status(row, trade_id, "rejected", "Rejected by exchange")
                _mirror_runtime(row)
                _append_event(row, f"Rolled back: order REJECTED ({client_ord_id})",
                              {"action": entry.get("action")})
                _mark_dirty()
            _pending_orders.pop(client_ord_id, None)
        elif status == "cancelled":
            m = _managers.get(sid)
            if m is not None:
                m.restore_runtime_state(entry["snapshot"])
            if row:
                if trade_id:
                    _update_trade_status(row, trade_id, "cancelled", "Cancelled")
                _mirror_runtime(row)
                _append_event(row, f"Rolled back: order CANCELLED ({client_ord_id})",
                              {"action": entry.get("action")})
                _mark_dirty()
            _pending_orders.pop(client_ord_id, None)
        elif status == "filled":
            if row:
                if trade_id:
                    _update_trade_status(row, trade_id, "filled", "Filled")
                _append_event(row, f"Order FILLED ({client_ord_id})",
                              {"action": entry.get("action")})
                _mark_dirty()
            _pending_orders.pop(client_ord_id, None)
        elif status == "partial_fill":
            if row:
                if trade_id:
                    _update_trade_status(row, trade_id, "partial_fill", "Partially filled")
                _append_event(row, f"Order partial ({client_ord_id})",
                              {"action": entry.get("action")})
                _mark_dirty()


# ──────────────────────────────────────────────────────────────────
# Diagnostics / test
# ──────────────────────────────────────────────────────────────────

def manager_for(strategy_id: str) -> Optional[PSXPositionManager]:
    return _managers.get(strategy_id)


def force_evaluate(
    strategy_id: str,
    tick: dict,
    *,
    fallback_pin: str,
    market_open: bool = True,
    place_callback: Optional[Callable[[TradeIntent], Optional[str]]] = None,
) -> dict:
    """
    Bypass throttles/cooldowns and run a single evaluation for diagnostics.
    Useful when the market is closed and you want to test the signal logic.

    If place_callback is None, any state transitions caused by this call are
    rolled back so testing never corrupts persisted position state.
    """
    with _lock:
        row = _row_for(strategy_id)
        if not row:
            raise KeyError(strategy_id)
        # Reset transient throttles + finish warmup so this tick is acted on.
        _last_eval_ts.pop(strategy_id, None)
        _last_exit_ts.pop(strategy_id, None)
        _last_placed_ts.pop(strategy_id, None)
        _warmup_counts[strategy_id] = int(row.get("warmup_ticks", 0)) + 1
        m = _managers.get(strategy_id)
        pre_snapshot = m.snapshot_runtime_state() if m else None

    intents = evaluate_for_symbol(
        row["symbol"], tick, market_open=market_open, fallback_pin=fallback_pin
    )
    placed = []
    if place_callback:
        for intent in intents:
            if intent.dry_run:
                placed.append({"intent": intent.action, "skipped": "dry_run"})
                continue
            try:
                ord_hash = place_callback(intent)
            except Exception as e:  # noqa: BLE001
                rollback_intent(intent, f"place error: {e}")
                placed.append({"intent": intent.action, "error": str(e)})
                continue
            if ord_hash:
                commit_intent_placed(intent, ord_hash)
                placed.append({"intent": intent.action, "ord_hash": ord_hash})
    else:
        # No real placement requested — restore manager to its prior state so
        # the test never causes a phantom position.
        with _lock:
            if pre_snapshot is not None and m is not None:
                m.restore_runtime_state(pre_snapshot)
                row2 = _row_for(strategy_id)
                if row2:
                    _mirror_runtime(row2)
                    if intents:
                        _append_event(
                            row2,
                            "Test-evaluate (no place): state restored",
                            {"intents": [i.action for i in intents]},
                        )
                    _mark_dirty()

    return {
        "strategy": _public_view(_row_for(strategy_id) or {}),
        "intents": [{"action": i.action, "dry_run": i.dry_run} for i in intents],
        "placed": placed,
    }

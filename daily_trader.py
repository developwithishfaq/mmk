"""
daily_trader.py — Intraday momentum + mean-reversion auto-trader.

Architecture
============

This module is a **decoupled** intraday trading bot. It does NOT import
server.py or mmk_api.py directly. Instead, callers inject a `Hooks`
object that provides everything the bot needs to interact with the
broker (place orders, fetch quotes, read cash, etc.).

This makes the bot:
- Testable in isolation (mock Hooks)
- Independent of transport (REST today, WebSocket tomorrow)
- Safe to run from any thread / process

Modules in this file
--------------------
1. ``Hooks``           — dependency injection for broker operations
2. ``Config``          — strategy parameters (risk, targets, time gates)
3. ``Position``        — bot-tracked open position state
4. ``Signal``          — scanner output
5. ``DailyTrader``     — the actual bot (state machine + loop)

State machine
-------------
- STOPPED   bot is idle, no thread
- RUNNING   thread is active, scanning + managing
- HALTED    kill switch tripped; no new entries; existing positions still
            managed for exit (defensive)

Per-position lifecycle
----------------------
- pending_entry  → buy order sent, awaiting fill
- holding        → position open, monitoring exit conditions
- pending_exit   → sell order sent, awaiting fill
- closed         → done (with realized P/L)

Persistence
-----------
State is saved to ``daily_trader_state.json`` (atomic write, debounced).
The bot can recover open positions on restart.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Karachi")
except Exception:   # pragma: no cover  (Py<3.9 fallback)
    TZ = None

log = logging.getLogger("mmk.daily")

STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "daily_trader_state.json",
)
SAVE_DEBOUNCE_SEC = 1.0
EVENT_LOG_LIMIT = 200
TRADE_LOG_LIMIT = 200
SIGNAL_LOG_LIMIT = 100


# ══════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════

@dataclass
class Hooks:
    """
    Dependency injection bundle. Server wires real broker callables
    here; tests can pass mocks.
    """
    # Returns latest tick dict {symbol, last, bid, ask, change, volume, ...}
    # or None if not subscribed.
    get_quote: Callable[[str], Optional[dict]]

    # Subscribes a list of symbols to the live feed (idempotent).
    subscribe_symbols: Callable[[list[str]], None]

    # Returns a list of feed items: [{symbol, last_price, net_change,
    # volume, bid_price, ask_price, ...}, ...]
    fetch_feed: Callable[[str, str], list[dict]]   # (feed_type, code)

    # Returns float available cash (or 0.0 on error).
    get_cash: Callable[[], float]

    # Place a limit buy. Returns ord_hash (str) or raises.
    place_limit_buy: Callable[[str, float, int], str]   # (symbol, price, qty)

    # Place a limit sell. Returns ord_hash.
    place_limit_sell: Callable[[str, float, int], str]

    # Place a market sell (panic exit). Returns ord_hash.
    place_market_sell: Callable[[str, int], str]

    # Cancel an order by ord_hash. Returns True if cancel was sent.
    cancel_order: Callable[[str], bool]

    # Optional: place a stop-loss (SLO). If None, the bot uses an
    # internal soft stop check instead.
    place_slo: Optional[Callable[[str, int, float, float], str]] = None
    # signature: (symbol, qty, trigger_price, stop_price) → ord_hash

    # ── Broker truth (cached BrokerView) ────────────────────────
    # Returns True if the broker has any outstanding order OR any
    # activity (queued/filled/partial/cancelled/rejected) on `symbol`
    # today, regardless of whether the bot placed it. Used as a hard
    # gate before any new entry.
    broker_has_any_today: Optional[Callable[[str], bool]] = None

    # Returns True if market_status == OPEN.
    broker_market_open: Optional[Callable[[], bool]] = None

    # Returns the broker's authoritative open positions list (rows from
    # /getopenposition). Used for periodic reconciliation.
    get_broker_positions: Optional[Callable[[], list[dict]]] = None

    # Force-refresh every BrokerView cache (manual Sync from Broker).
    broker_refresh: Optional[Callable[[], dict]] = None


@dataclass
class Config:
    """Strategy parameters. Editable via API at runtime."""

    enabled:               bool   = True

    # ── Universe ────────────────────────────────────────────────
    feed_type:             str    = "I"     # I=index, F=futures, S=sector
    feed_code:             str    = "0"     # 0=KSE-100, 1=KSE-30 for index feed
    extra_watchlist:       list[str] = field(default_factory=list)

    # ── Entry signal ────────────────────────────────────────────
    min_change_pct:        float  = 0.5     # require change% >= +0.5% (early, not late)
    min_volume:            int    = 200_000 # decent liquidity floor
    max_spread_pct:        float  = 0.5     # reject if (ask-bid)/bid > 0.5%
    min_price:             float  = 10.0    # avoid very cheap stocks
    max_price:             float  = 55.0    # max stock price to consider (PKR)

    # ── Position sizing ──────────────────────────────────────────
    risk_per_trade_pct:    float  = 2.0     # 2% of cash risked per trade (primary formula)
    max_concurrent:        int    = 3           # max simultaneous open positions
    # Safety caps (PKR hard limits, applied after the risk formula)
    max_orders_per_day:    int    = 6           # max entry orders per day
    max_buy_amount_per_trade:  float = 1_000.0   # max PKR on a single buy
    max_sell_amount_per_trade: float = 1_000.0   # max PKR on a single sell
    daily_max_buy_amount:      float = 10_000.0  # max PKR total buys today
    daily_max_sell_amount:     float = 10_000.0  # max PKR total sells today

    # ── Entry signal (upper bound) ───────────────────────────────
    # Reject stocks already up more than this — move likely exhausted near
    # PSX's 7.5 % upper circuit limit.
    max_change_pct:        float  = 5.0

    # ── VWAP filter ──────────────────────────────────────────────
    # When True, only enter if the current price is within vwap_max_above_pct
    # of the intraday VWAP.  Stocks trading well above VWAP have already moved;
    # stocks at or below VWAP offer a better entry at institutional fair value.
    use_vwap_filter:       bool   = True
    vwap_max_above_pct:    float  = 0.5    # allow entry up to +0.5 % above VWAP

    # ── Tick-driven entry ─────────────────────────────────────────
    # When True the bot reacts to live MF ticks (~200 ms latency) in addition
    # to the 30-second poll scan.  The poll scan still runs to discover symbols
    # not yet subscribed; ticks provide sub-second re-evaluation of subscribed
    # symbols so we don't miss a brief entry window.
    tick_driven:           bool   = True

    # ── Exit ────────────────────────────────────────────────────
    target_pct:            float  = 2.0     # take profit at +2% (2:1 R:R with 1% stop)
    stop_pct:              float  = 1.0     # stop loss at -1%
    trailing_stop_pct:     float  = 0.0     # 0 = disabled
    use_slo:               bool   = False   # if True and Hooks.place_slo set

    # ── Daily risk ──────────────────────────────────────────────
    daily_loss_limit_pct:  float  = 3.0     # halt if bot P/L hits -3% of start cash
    cooldown_after_loss_s: int    = 300     # 5 min cooldown after a losing trade

    # ── Time gates (24h, PKT) ───────────────────────────────────
    entry_start_hhmm:      str    = "09:35"  # let market settle 5 min after open
    entry_stop_hhmm:       str    = "14:00"  # no new entries after 2 PM
    force_exit_hhmm:       str    = "15:15"  # close all 15 min before market close

    # ── Loop cadence ────────────────────────────────────────────
    scan_interval_sec:     int    = 10
    monitor_interval_sec:  int    = 10

    # ── Order placement ─────────────────────────────────────────
    entry_offset_pct:      float  = 0.05    # buy at ask + 0.05% to ensure fill
    exit_offset_pct:       float  = 0.05    # sell at bid - 0.05%

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        import dataclasses
        fields = {f.name for f in dataclasses.fields(cls)}
        clean = {k: d[k] for k in d if k in fields}
        if "extra_watchlist" in clean and not isinstance(clean["extra_watchlist"], list):
            clean["extra_watchlist"] = []
        return cls(**clean)


@dataclass
class Position:
    """Bot-tracked open position."""
    pos_id:            str
    symbol:            str
    side:              str   # "buy" (we only go long for now)
    qty:               int   # ordered/intended qty (entry); after fill, equals filled_qty
    entry_price:       float # AvgPx of cumulative entry fills (VWAP)
    target_price:      float
    stop_price:        float
    status:            str   # pending_entry | holding | pending_exit | closed
    entry_ord_hash:    str   = ""
    exit_ord_hash:     str   = ""
    exit_order_qty:    int   = 0    # qty requested in current exit order
    slo_ord_hash:      str   = ""
    entry_filled_ts:   float = 0.0
    exit_price:        float = 0.0
    exit_reason:       str   = ""   # target | stop | time_cutoff | manual | broker_drift
    realized_pl:       float = 0.0
    # Partial-fill bookkeeping
    filled_qty:        int   = 0    # cumulative entry fill qty
    avg_fill_price:    float = 0.0  # entry VWAP
    exit_filled_qty:   int   = 0    # cumulative exit fill qty
    exit_avg_price:    float = 0.0  # exit VWAP
    # SLO cancel retry bookkeeping
    slo_cancel_attempts: int   = 0
    slo_cancel_pending_ts: float = 0.0
    created_ts:        float = field(default_factory=time.time)
    updated_ts:        float = field(default_factory=time.time)

    def mtm_pl(self, last_price: float) -> float:
        if self.status == "pending_entry":
            return 0.0
        if self.qty <= 0 or self.entry_price <= 0:
            return 0.0
        return (last_price - self.entry_price) * self.qty

    def mtm_pl_pct(self, last_price: float) -> float:
        if self.status == "pending_entry" or self.entry_price <= 0:
            return 0.0
        return (last_price - self.entry_price) / self.entry_price * 100.0


@dataclass
class Signal:
    """Scanner output."""
    symbol:      str
    last_price:  float
    change_pct:  float
    volume:      int
    bid:         float
    ask:         float
    spread_pct:  float
    score:       float
    vwap:        float = 0.0   # intraday VWAP at signal time (0 = not yet computed)
    ts:          float = field(default_factory=time.time)
    rejected:    str = ""   # reason if rejected during validation


@dataclass
class VwapTracker:
    """
    Per-symbol intraday VWAP approximation built from the live MF tick feed.

    The broker sends cumulative daily volume on each tick (not per-trade volume),
    so we compute delta-volume between consecutive ticks and weight each tick's
    last price by that delta.  This is an approximation of true VWAP (which
    requires every individual trade print) but is accurate enough for signal
    filtering on PSX.

    Reset daily: DailyTrader.on_tick() replaces the tracker when day_key changes.
    """
    day_key:      str   = ""
    pv_sum:       float = 0.0   # Σ (price × delta_volume)
    vol_sum:      float = 0.0   # Σ delta_volume
    prev_cum_vol: float = 0.0   # last seen cumulative volume (for delta calc)

    def update(self, last_price: float, cum_volume: float) -> None:
        delta = max(0.0, cum_volume - self.prev_cum_vol)
        if delta > 0 and last_price > 0:
            self.pv_sum  += last_price * delta
            self.vol_sum += delta
        self.prev_cum_vol = cum_volume

    @property
    def vwap(self) -> float:
        return self.pv_sum / self.vol_sum if self.vol_sum > 0 else 0.0


# ══════════════════════════════════════════════════════════════════
# DAILY TRADER
# ══════════════════════════════════════════════════════════════════

class DailyTrader:
    """Singleton-style intraday auto trader."""

    def __init__(self, hooks: Optional[Hooks] = None):
        self._hooks: Optional[Hooks] = hooks
        self._cfg = Config()

        self._lock = threading.RLock()
        self._state = "STOPPED"          # STOPPED | RUNNING | HALTED
        self._halt_reason = ""

        self._positions: dict[str, Position] = {}        # pos_id → Position
        self._ord_to_pos: dict[str, str] = {}            # ord_hash → pos_id

        self._signals: list[Signal] = []
        self._events: list[dict] = []
        self._closed_trades: list[dict] = []

        self._day_key = self._today_key()
        self._start_of_day_cash: Optional[float] = None
        self._last_loss_ts: float = 0.0
        self._trade_count_today: int = 0
        self._daily_buy_value: float = 0.0
        self._daily_sell_value: float = 0.0

        self._loop_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._dirty = False
        self._dirty_event = threading.Event()

        # ── Tick-driven infrastructure ───────────────────────────
        # Per-symbol VWAP accumulators (rebuilt intraday from live ticks).
        self._vwap_data: dict[str, VwapTracker] = {}
        # Per-symbol timestamp of last tick-driven signal evaluation
        # (throttles redundant evaluations to once every 2 s per symbol).
        self._tick_eval_ts: dict[str, float] = {}
        # Non-reentrant lock: only one entry attempt (poll or tick) at a time.
        self._entry_lock = threading.Lock()
        self._save_thread: Optional[threading.Thread] = None
        self._save_thread_stop = threading.Event()

    # ── Hooks injection (called by server.py after login) ───────

    def set_hooks(self, hooks: Hooks) -> None:
        with self._lock:
            self._hooks = hooks

    # ── Persistence ─────────────────────────────────────────────

    def _atomic_write(self, data: dict) -> None:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, STATE_FILE)

    def _serialize(self) -> dict:
        with self._lock:
            return {
                "version": 1,
                "config": asdict(self._cfg),
                "state": self._state,
                "halt_reason": self._halt_reason,
                "day_key": self._day_key,
                "start_of_day_cash": self._start_of_day_cash,
                "trade_count_today": self._trade_count_today,
                "daily_buy_value": self._daily_buy_value,
                "daily_sell_value": self._daily_sell_value,
                "last_loss_ts": self._last_loss_ts,
                "positions": [asdict(p) for p in self._positions.values()],
                "closed_trades": list(self._closed_trades)[-TRADE_LOG_LIMIT:],
                "events": list(self._events)[-EVENT_LOG_LIMIT:],
            }

    def _deserialize(self, data: dict) -> None:
        with self._lock:
            cfg = data.get("config")
            if isinstance(cfg, dict):
                try:
                    self._cfg = Config.from_dict(cfg)
                except Exception:
                    pass
            self._state = data.get("state", "STOPPED")
            if self._state == "RUNNING":
                self._state = "STOPPED"   # don't auto-resume on restart
            self._halt_reason = data.get("halt_reason", "")
            self._day_key = data.get("day_key", self._today_key())
            self._start_of_day_cash = data.get("start_of_day_cash")
            self._trade_count_today = int(data.get("trade_count_today") or 0)
            self._daily_buy_value = float(data.get("daily_buy_value") or 0.0)
            self._daily_sell_value = float(data.get("daily_sell_value") or 0.0)
            self._last_loss_ts = float(data.get("last_loss_ts") or 0.0)
            self._positions = {}
            for prow in data.get("positions", []):
                try:
                    p = Position(**prow)
                    self._positions[p.pos_id] = p
                    if p.entry_ord_hash:
                        self._ord_to_pos[p.entry_ord_hash] = p.pos_id
                    if p.exit_ord_hash:
                        self._ord_to_pos[p.exit_ord_hash] = p.pos_id
                except Exception as e:
                    log.warning(f"failed to deserialize position: {e}")
            self._closed_trades = list(data.get("closed_trades", []))
            self._events = list(data.get("events", []))

    def init(self) -> None:
        """Load state from disk (called on server boot)."""
        if not os.path.exists(STATE_FILE):
            self._save_thread_start()
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._deserialize(data)
        except Exception as e:
            log.warning(f"failed to load daily_trader_state.json: {e}")
        self._save_thread_start()

    def shutdown(self) -> None:
        self.stop()
        self._save_thread_stop.set()
        self._dirty_event.set()
        if self._save_thread and self._save_thread.is_alive():
            self._save_thread.join(timeout=3.0)
        self._flush()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._dirty_event.set()

    def _flush(self) -> None:
        try:
            data = self._serialize()
            self._atomic_write(data)
            self._dirty = False
        except Exception as e:
            log.warning(f"daily_trader: state save failed: {e}")

    def _save_loop(self) -> None:
        while not self._save_thread_stop.is_set():
            self._dirty_event.wait(timeout=2.0)
            self._dirty_event.clear()
            if self._dirty:
                time.sleep(SAVE_DEBOUNCE_SEC)
                self._flush()

    def _save_thread_start(self) -> None:
        if self._save_thread and self._save_thread.is_alive():
            return
        self._save_thread_stop.clear()
        t = threading.Thread(
            target=self._save_loop,
            name="daily-trader-saver",
            daemon=True,
        )
        t.start()
        self._save_thread = t

    # ── Logging helpers ────────────────────────────────────────

    def _emit(self, level: str, msg: str, detail: Optional[dict] = None) -> None:
        evt = {
            "ts":     time.time(),
            "level":  level,
            "msg":    msg,
            "detail": detail or {},
        }
        with self._lock:
            self._events.append(evt)
            if len(self._events) > EVENT_LOG_LIMIT:
                self._events = self._events[-EVENT_LOG_LIMIT:]
        if level == "error":
            log.error(f"[daily] {msg}  {detail or ''}")
        elif level == "warn":
            log.warning(f"[daily] {msg}  {detail or ''}")
        else:
            log.info(f"[daily] {msg}  {detail or ''}")
        self._mark_dirty()

    # ── Time / day rollover ─────────────────────────────────────

    @staticmethod
    def _now_local() -> datetime:
        if TZ is not None:
            return datetime.now(TZ)
        return datetime.now()

    @staticmethod
    def _today_key() -> str:
        return DailyTrader._now_local().strftime("%Y-%m-%d")

    @staticmethod
    def _now_hhmm() -> str:
        return DailyTrader._now_local().strftime("%H:%M")

    @staticmethod
    def _start_of_day_ts(day_key: str) -> float:
        """Unix ts for 00:00 in the market timezone for the given YYYY-MM-DD."""
        try:
            d = datetime.strptime(day_key, "%Y-%m-%d")
            if TZ is not None:
                d = d.replace(tzinfo=TZ)
            return d.timestamp()
        except Exception:
            return 0.0

    def _check_day_rollover(self) -> None:
        today = self._today_key()
        if today != self._day_key:
            with self._lock:
                self._emit("info", f"new trading day {today}", {"prev_day": self._day_key})
                self._day_key = today
                self._trade_count_today = 0
                self._daily_buy_value = 0.0
                self._daily_sell_value = 0.0
                self._last_loss_ts = 0.0
                self._start_of_day_cash = None
                self._vwap_data.clear()
                self._tick_eval_ts.clear()
                if self._state == "HALTED":
                    self._state = "STOPPED"
                    self._halt_reason = ""

    # ── Public control API ──────────────────────────────────────

    def start(self) -> dict:
        with self._lock:
            if self._state == "RUNNING":
                return {"ok": False, "msg": "already running"}
            if not self._hooks:
                return {"ok": False, "msg": "hooks not configured (login first)"}
            self._check_day_rollover()
            if self._start_of_day_cash is None:
                try:
                    self._start_of_day_cash = float(self._hooks.get_cash() or 0.0)
                except Exception:
                    self._start_of_day_cash = 0.0
            self._state = "RUNNING"
            self._stop_event.clear()
        self._emit("info", "daily trader STARTED", {
            "start_cash": self._start_of_day_cash,
            "config": asdict(self._cfg),
        })
        self._loop_thread = threading.Thread(
            target=self._loop, name="daily-trader", daemon=True,
        )
        self._loop_thread.start()
        return {"ok": True, "msg": "started"}

    def stop(self) -> dict:
        was_running = False
        with self._lock:
            if self._state == "RUNNING":
                self._state = "STOPPED"
                was_running = True
            self._stop_event.set()
        if was_running:
            self._emit("info", "daily trader STOPPED")
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=5.0)
        return {"ok": True, "msg": "stopped"}

    def halt(self, reason: str = "manual") -> dict:
        with self._lock:
            self._state = "HALTED"
            self._halt_reason = reason
        self._emit("warn", f"daily trader HALTED: {reason}")
        return {"ok": True, "msg": "halted", "reason": reason}

    def reset_day(self) -> dict:
        """Force-reset daily counters (e.g. start of trading day)."""
        with self._lock:
            self._day_key = self._today_key()
            self._trade_count_today = 0
            self._daily_buy_value = 0.0
            self._daily_sell_value = 0.0
            self._last_loss_ts = 0.0
            self._start_of_day_cash = None
            if self._state == "HALTED":
                self._state = "STOPPED"
                self._halt_reason = ""
        self._emit("info", "day counters reset")
        return {"ok": True}

    def get_config(self) -> dict:
        return asdict(self._cfg)

    def set_config(self, patch: dict) -> dict:
        with self._lock:
            current = asdict(self._cfg)
            current.update({k: v for k, v in patch.items() if k in current})
            try:
                self._cfg = Config.from_dict(current)
            except Exception as e:
                return {"ok": False, "msg": f"invalid config: {e}"}
        self._emit("info", "config updated", {"patch": patch})
        return {"ok": True, "config": asdict(self._cfg)}

    def status(self) -> dict:
        with self._lock:
            cash = None
            try:
                cash = float(self._hooks.get_cash() or 0.0) if self._hooks else None
            except Exception:
                cash = None
            realized, unrealized = self._bot_day_pl()
            day_pl = round(realized + unrealized, 2)
            day_pl_pct = None
            if self._start_of_day_cash:
                day_pl_pct = round(day_pl / self._start_of_day_cash * 100.0, 3)
            open_positions = [
                self._position_view(p, self._latest_price(p.symbol))
                for p in self._positions.values()
                if p.status != "closed"
            ]
            market_status = "UNKNOWN"
            try:
                if self._hooks and self._hooks.broker_market_open is not None:
                    market_status = "OPEN" if self._hooks.broker_market_open() else "CLOSED"
            except Exception:
                pass
            return {
                "state":              self._state,
                "halt_reason":        self._halt_reason,
                "day_key":            self._day_key,
                "now":                self._now_local().strftime("%Y-%m-%d %H:%M:%S %Z"),
                "tz":                 str(TZ) if TZ else "local",
                "market_status":      market_status,
                "start_of_day_cash":  self._start_of_day_cash,
                "current_cash":       cash,
                "day_pl":             day_pl,
                "day_pl_realized":    round(realized, 2),
                "day_pl_unrealized":  round(unrealized, 2),
                "day_pl_pct":         day_pl_pct,
                "trade_count_today":  self._trade_count_today,
                "daily_buy_value":    round(self._daily_buy_value, 2),
                "daily_sell_value":   round(self._daily_sell_value, 2),
                "open_positions":     open_positions,
                "open_count":         len(open_positions),
                "config":             asdict(self._cfg),
            }

    def list_signals(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return [asdict(s) for s in self._signals[-limit:]]

    def list_positions(self, include_closed: bool = False) -> list[dict]:
        with self._lock:
            out = []
            for p in self._positions.values():
                if not include_closed and p.status == "closed":
                    continue
                out.append(self._position_view(p, self._latest_price(p.symbol)))
            return out

    def list_trades(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._closed_trades[-limit:])

    def list_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._events[-limit:])

    def manual_sync(self) -> dict:
        """
        Force-refresh broker caches and run a reconciliation pass.
        Used by the 'Sync from Broker' UI button.
        """
        info: dict = {}
        if self._hooks and self._hooks.broker_refresh is not None:
            try:
                info = self._hooks.broker_refresh() or {}
            except Exception as e:
                self._emit("warn", "broker_refresh failed", {"err": str(e)})
        try:
            self._reconcile_with_broker()
        except Exception as e:
            self._emit("warn", "reconcile failed", {"err": str(e)})
        self._emit("info", "manual sync complete", info)
        return {"ok": True, "snapshot": info}

    def manual_close(self, pos_id: str, reason: str = "manual") -> dict:
        with self._lock:
            pos = self._positions.get(pos_id)
            if not pos:
                return {"ok": False, "msg": "position not found"}
            if pos.status not in ("holding", "pending_entry"):
                return {"ok": False, "msg": f"cannot close in status {pos.status}"}
            if pos.status == "pending_entry":
                if pos.entry_ord_hash and self._hooks:
                    try:
                        self._hooks.cancel_order(pos.entry_ord_hash)
                        return {"ok": True, "msg": "cancel submitted"}
                    except Exception as e:
                        return {"ok": False, "msg": f"cancel failed: {e}"}
                return {"ok": False, "msg": "no pending order to cancel"}
        self._exit_position(pos, reason)
        return {"ok": True, "msg": "exit submitted"}

    # ── Order callback (server.py invokes on FIX execution report) ──

    def on_order_update(
        self,
        ord_hash:   str,
        status:     str,
        fill_price: float = 0.0,
        fill_qty:   int   = 0,
        cum_qty:    int   = 0,
        avg_px:     float = 0.0,
        symbol:     str   = "",
    ) -> None:
        """
        Called by server.py when a FIX execution report arrives.

        FIX tag mapping:
          fill_price = LastPx (tag 31)        — last fill on this report
          fill_qty   = LastQty (tag 32)       — last fill qty on this report
          cum_qty    = CumQty (tag 14)        — cumulative filled qty
          avg_px     = AvgPx (tag 6)          — cumulative VWAP
        """
        with self._lock:
            pos_id = self._ord_to_pos.get(ord_hash)
            if not pos_id:
                return
            pos = self._positions.get(pos_id)
            if not pos:
                return
            is_entry = (ord_hash == pos.entry_ord_hash)
            is_exit  = (ord_hash == pos.exit_ord_hash)

        # ── Entry side ──────────────────────────────────────────
        if is_entry:
            if status in ("filled", "partial_fill"):
                with self._lock:
                    old_filled = pos.filled_qty
                    new_filled = cum_qty if cum_qty > 0 else (pos.filled_qty + max(fill_qty, 0))
                    new_avg    = avg_px if avg_px > 0 else (
                        fill_price if fill_price > 0 else pos.avg_fill_price
                    )
                    pos.filled_qty = new_filled
                    if new_avg > 0:
                        pos.avg_fill_price = new_avg
                        pos.entry_price    = new_avg
                        pos.target_price   = round(new_avg * (1 + self._cfg.target_pct / 100), 2)
                        pos.stop_price     = round(new_avg * (1 - self._cfg.stop_pct  / 100), 2)
                    if pos.filled_qty > 0:
                        pos.qty = pos.filled_qty
                        pos.status = "holding"
                        if not pos.entry_filled_ts:
                            pos.entry_filled_ts = time.time()
                    # Track actual filled buy notional for daily safety caps.
                    filled_delta = max(0, pos.filled_qty - old_filled)
                    if filled_delta > 0:
                        px = pos.avg_fill_price or pos.entry_price or fill_price or 0.0
                        self._daily_buy_value = round(self._daily_buy_value + (filled_delta * px), 4)
                    pos.updated_ts = time.time()
                self._emit(
                    "info",
                    f"ENTRY {'filled' if status == 'filled' else 'partial'} {pos.symbol}",
                    {
                        "pos_id":     pos.pos_id,
                        "filled_qty": pos.filled_qty,
                        "avg_price":  pos.avg_fill_price,
                        "target":     pos.target_price,
                        "stop":       pos.stop_price,
                    },
                )
                self._mark_dirty()
                if status == "filled":
                    self._maybe_place_slo(pos)
                return

            if status in ("rejected", "cancelled"):
                # Partial entry then cancel → keep what filled, transition to holding.
                with self._lock:
                    if pos.filled_qty > 0:
                        pos.qty = pos.filled_qty
                        if pos.avg_fill_price > 0 and pos.entry_price <= 0:
                            pos.entry_price = pos.avg_fill_price
                        pos.status = "holding"
                        pos.updated_ts = time.time()
                        keep_partial = True
                    else:
                        pos.status = "closed"
                        pos.exit_reason = f"entry_{status}"
                        pos.updated_ts = time.time()
                        # Roll back the daily trade counter — this entry never
                        # actually went through, so it shouldn't burn the cap.
                        if self._trade_count_today > 0:
                            self._trade_count_today -= 1
                        keep_partial = False
                if keep_partial:
                    self._emit(
                        "warn",
                        f"ENTRY {status} after partial — keeping {pos.filled_qty} {pos.symbol}",
                        {"pos_id": pos.pos_id, "filled_qty": pos.filled_qty},
                    )
                    self._mark_dirty()
                    self._maybe_place_slo(pos)
                else:
                    self._emit("warn", f"ENTRY {status} {pos.symbol}", {"pos_id": pos.pos_id})
                    self._archive_position(pos)
                return

        # ── Exit side ───────────────────────────────────────────
        if is_exit:
            if status in ("filled", "partial_fill"):
                with self._lock:
                    old_exit = pos.exit_filled_qty
                    new_exit = cum_qty if cum_qty > 0 else (pos.exit_filled_qty + max(fill_qty, 0))
                    new_xpx  = avg_px if avg_px > 0 else (
                        fill_price if fill_price > 0 else pos.exit_avg_price
                    )
                    pos.exit_filled_qty = new_exit
                    if new_xpx > 0:
                        pos.exit_avg_price = new_xpx
                    # Track actual filled sell notional for daily safety caps.
                    exit_delta = max(0, pos.exit_filled_qty - old_exit)
                    if exit_delta > 0:
                        px = pos.exit_avg_price or fill_price or pos.entry_price or 0.0
                        self._daily_sell_value = round(self._daily_sell_value + (exit_delta * px), 4)
                    pos.updated_ts = time.time()
                    target_qty = pos.exit_order_qty if pos.exit_order_qty > 0 else pos.qty
                    fully_done = pos.exit_filled_qty >= target_qty
                if fully_done:
                    self._finalize_exit(pos, fill_price=pos.exit_avg_price or fill_price)
                else:
                    self._emit(
                        "info",
                        f"EXIT partial {pos.symbol}",
                        {
                            "pos_id":     pos.pos_id,
                            "filled":     pos.exit_filled_qty,
                            "of":         pos.qty,
                            "avg_price":  pos.exit_avg_price,
                        },
                    )
                    self._mark_dirty()
                return

            if status in ("rejected", "cancelled"):
                with self._lock:
                    target_qty = pos.exit_order_qty if pos.exit_order_qty > 0 else pos.qty
                    if pos.exit_filled_qty > 0 and pos.exit_filled_qty < target_qty:
                        # Partial exit then cancel — adjust position size and
                        # treat the rest as still held; main loop will retry exit.
                        pos.qty = max(0, pos.qty - pos.exit_filled_qty)
                        pos.realized_pl = round(
                            pos.realized_pl + (pos.exit_avg_price - pos.entry_price) * pos.exit_filled_qty,
                            2,
                        )
                        pos.exit_filled_qty = 0
                        pos.exit_avg_price = 0.0
                        pos.exit_order_qty = 0
                        pos.exit_ord_hash = ""
                        pos.status = "holding"
                        pos.updated_ts = time.time()
                    else:
                        pos.status = "holding"
                        pos.exit_ord_hash = ""
                        pos.exit_filled_qty = 0
                        pos.exit_order_qty = 0
                        pos.updated_ts = time.time()
                self._emit("warn", f"EXIT {status} {pos.symbol}, will retry", {"pos_id": pos.pos_id})
                self._mark_dirty()
                return

    # ── Tick-driven entry (called by socket_handler on every MF tick) ─

    def on_tick(self, symbol: str, tick: dict) -> None:
        """
        Called by socket_handler.on_message on every live MF/FMA price tick.

        Two responsibilities:

        1. **VWAP accumulation** — update the per-symbol VwapTracker so that
           every signal evaluation (poll *and* tick-driven) has fresh VWAP data.
           This happens unconditionally, even when the bot is STOPPED.

        2. **Tick-driven entry** — when the bot is RUNNING and tick_driven=True,
           evaluate an entry signal immediately on the tick (~200 ms latency)
           instead of waiting up to 30 seconds for the poll scan.  A per-symbol
           2-second throttle avoids churning on burst ticks for the same symbol.
        """
        if not symbol or not tick:
            return

        # ── 1. Update VWAP accumulator ───────────────────────────────────────
        last    = float(tick.get("last") or tick.get("last_price") or 0.0)
        cum_vol = float(tick.get("volume") or 0.0)
        today   = self._today_key()

        with self._lock:
            tracker = self._vwap_data.get(symbol)
            if tracker is None or tracker.day_key != today:
                tracker = VwapTracker(day_key=today)
                self._vwap_data[symbol] = tracker
            if last > 0 and cum_vol > 0:
                tracker.update(last, cum_vol)
            vwap = tracker.vwap

        # ── 2. Tick-driven entry evaluation ─────────────────────────────────
        with self._lock:
            state       = self._state
            tick_driven = self._cfg.tick_driven

        if state != "RUNNING" or not tick_driven:
            return

        # Per-symbol throttle: evaluate at most once every 2 seconds.
        now = time.time()
        with self._lock:
            last_eval = self._tick_eval_ts.get(symbol, 0.0)
        if now - last_eval < 2.0:
            return
        with self._lock:
            self._tick_eval_ts[symbol] = now

        if not self._can_take_new_entry():
            return
        if not self._hooks:
            return

        cfg = self._cfg
        row = {
            "symbol":     symbol,
            "last_price": tick.get("last") or tick.get("last_price"),
            "net_change": tick.get("change") or tick.get("net_change"),
            "volume":     tick.get("volume"),
            "bid_price":  tick.get("bid") or tick.get("bid_price"),
            "ask_price":  tick.get("ask") or tick.get("ask_price"),
        }
        sig = self._build_signal_from_row(row, cfg, vwap=vwap)
        if sig is None or sig.rejected:
            return

        # Non-blocking acquire — the poll scanner or another concurrent tick
        # may already be executing an entry.  Skip rather than queue.
        if not self._entry_lock.acquire(blocking=False):
            return
        try:
            # Re-check after acquiring: state may have changed.
            if not self._can_take_new_entry():
                return
            self._enter_position(sig)
        finally:
            self._entry_lock.release()

    # ── Loop ───────────────────────────────────────────────────

    def _loop(self) -> None:
        last_scan = 0.0
        last_reconcile = 0.0
        RECONCILE_INTERVAL_SEC = 60
        while not self._stop_event.is_set():
            try:
                self._check_day_rollover()
                with self._lock:
                    state = self._state
                if state != "RUNNING":
                    break

                # 1. Manage existing positions every monitor tick
                self._manage_open_positions()

                # 2. Retry any pending SLO cancels (broker IDs may have arrived)
                self._retry_slo_cancels()

                # 3. Periodic reconciliation against broker truth (every 60s)
                now = time.time()
                if now - last_reconcile >= RECONCILE_INTERVAL_SEC:
                    last_reconcile = now
                    self._reconcile_with_broker()

                # 4. Scan for new signals at scanner cadence
                if now - last_scan >= self._cfg.scan_interval_sec:
                    last_scan = now
                    if self._can_take_new_entry():
                        self._scan_and_enter()

                # 5. Force-exit if past cutoff
                self._enforce_force_exit()

            except Exception as e:
                self._emit("error", "loop iteration failed", {"err": str(e)})

            self._stop_event.wait(timeout=self._cfg.monitor_interval_sec)

        log.info("[daily] loop thread exiting")

    # ── Risk & gating ──────────────────────────────────────────

    def _can_take_new_entry(self) -> bool:
        if self._state != "RUNNING":
            return False
        cfg = self._cfg
        if not cfg.enabled:
            return False
        now_hhmm = self._now_hhmm()
        if now_hhmm < cfg.entry_start_hhmm or now_hhmm > cfg.entry_stop_hhmm:
            return False
        max_orders = int(cfg.max_orders_per_day or 0)
        if max_orders > 0 and self._trade_count_today >= max_orders:
            return False
        # Cooldown after loss
        if self._last_loss_ts and (time.time() - self._last_loss_ts) < cfg.cooldown_after_loss_s:
            return False
        # Concurrent positions cap
        with self._lock:
            open_n = sum(
                1 for p in self._positions.values()
                if p.status in ("pending_entry", "holding", "pending_exit")
            )
        if open_n >= cfg.max_concurrent:
            return False
        # Market must be OPEN
        if self._hooks and self._hooks.broker_market_open is not None:
            try:
                if not self._hooks.broker_market_open():
                    return False
            except Exception:
                pass
        # Daily loss kill switch
        if self._is_daily_loss_breached():
            self.halt("daily loss limit reached")
            return False
        return True

    def _bot_day_pl(self) -> tuple[float, float]:
        """Return (realized, unrealized) P/L for bot positions today only."""
        start_ts = self._start_of_day_ts(self._day_key)
        realized = 0.0
        unrealized = 0.0
        with self._lock:
            for t in self._closed_trades:
                if (t.get("created_ts") or 0) >= start_ts:
                    realized += float(t.get("realized_pl") or 0.0)
            for p in self._positions.values():
                if p.status in ("holding", "pending_exit") and p.created_ts >= start_ts:
                    last = self._latest_price(p.symbol)
                    if last > 0:
                        unrealized += p.mtm_pl(last)
        return realized, unrealized

    def _is_daily_loss_breached(self) -> bool:
        if not self._start_of_day_cash:
            return False
        realized, unrealized = self._bot_day_pl()
        pl_pct = (realized + unrealized) / max(self._start_of_day_cash, 1.0) * 100
        return pl_pct <= -self._cfg.daily_loss_limit_pct

    # ── Scanner + Validator + Sizer + Entry ────────────────────

    def _build_signal_from_row(
        self,
        row:  dict,
        cfg:  Config,
        vwap: float = 0.0,
    ) -> Optional["Signal"]:
        """
        Convert a feed/quote row into a Signal with filter reasons applied.

        vwap — intraday VWAP for the symbol at this moment (0 = not yet available).
               Used by the VWAP filter and incorporated into the score.
        """
        sym = (row.get("symbol") or "").upper()
        if not sym:
            return None
        last = float(row.get("last_price") or row.get("last") or 0.0)
        chg  = float(row.get("net_change") or row.get("change") or 0.0)
        vol  = int(float(row.get("volume") or 0))
        bid  = float(row.get("bid_price") or row.get("bid") or 0.0)
        ask  = float(row.get("ask_price") or row.get("ask") or 0.0)
        if last <= 0:
            return None
        chg_pct = (chg / max(last - chg, 0.01)) * 100 if last - chg > 0 else 0.0
        spread  = ((ask - bid) / bid * 100) if bid > 0 else 999.0

        # ── Score: momentum × liquidity × VWAP-proximity bonus ──────────
        # Stocks at or below VWAP are buying near fair institutional value and
        # score higher; stocks far above VWAP score lower (move already done).
        # Formula: chg_pct × log10(vol)/5 × vwap_bonus
        vol_factor = math.log10(max(vol, 1)) / 5.0
        vwap_bonus = 1.0
        if vwap > 0:
            pct_above_vwap = (last - vwap) / vwap * 100.0
            # +10 % bonus per 0.1 % *below* VWAP; −10 % penalty per 0.1 % *above*.
            vwap_bonus = max(0.2, 1.0 - pct_above_vwap * 0.1)
        score = chg_pct * vol_factor * vwap_bonus

        sig = Signal(
            symbol=sym, last_price=last, change_pct=chg_pct,
            volume=vol, bid=bid, ask=ask, spread_pct=spread,
            score=score, vwap=vwap,
        )

        reasons = []
        if chg_pct < cfg.min_change_pct:              reasons.append("change<min")
        if chg_pct > cfg.max_change_pct:              reasons.append("change>max")
        if vol < cfg.min_volume:                      reasons.append("volume<min")
        if last < cfg.min_price:                      reasons.append("price<min")
        if last > cfg.max_price:                      reasons.append("price>max")
        if spread > cfg.max_spread_pct:               reasons.append("spread>max")
        if bid <= 0 or ask <= 0:                      reasons.append("no_quote")
        if any(p.symbol == sym and p.status != "closed"
               for p in self._positions.values()):
            reasons.append("already_held")

        # VWAP filter: reject if price is more than vwap_max_above_pct above VWAP.
        # Skip the filter when VWAP is not yet built (early morning, first ticks).
        if cfg.use_vwap_filter and vwap > 0:
            pct_above_vwap = (last - vwap) / vwap * 100.0
            if pct_above_vwap > cfg.vwap_max_above_pct:
                reasons.append("above_vwap")

        # Broker-truth gate (via has_blocking_order_for under the hood):
        #   BLOCK  — live outstanding order (not yet terminal), OR filled/partial today.
        #   ALLOW  — only rejected or cancelled history; those had zero economic effect.
        if self._hooks and self._hooks.broker_has_any_today is not None:
            try:
                if self._hooks.broker_has_any_today(sym):
                    reasons.append("broker_busy")
            except Exception:
                pass

        if reasons:
            sig.rejected = ",".join(reasons)
        return sig

    def _scan_and_enter(self) -> None:
        if not self._hooks:
            return
        cfg = self._cfg
        candidates: list[Signal] = []
        seen: set[str] = set()
        today = self._today_key()

        # 1) Pull full market feed
        try:
            feed = self._hooks.fetch_feed(cfg.feed_type, cfg.feed_code) or []
        except Exception as e:
            self._emit("warn", "feed fetch failed", {"err": str(e)})
            feed = []

        for row in feed:
            sym = (row.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            with self._lock:
                tracker = self._vwap_data.get(sym)
                vwap = tracker.vwap if (tracker and tracker.day_key == today) else 0.0
            sig = self._build_signal_from_row(row, cfg, vwap=vwap)
            if sig is None:
                continue
            seen.add(sym)
            candidates.append(sig)

        # 2) Merge in extra_watchlist symbols using live quotes
        for sym in (cfg.extra_watchlist or []):
            sym_u = (sym or "").upper().strip()
            if not sym_u or sym_u in seen:
                continue
            try:
                quote = self._hooks.get_quote(sym_u) or {}
            except Exception:
                quote = {}
            if not quote:
                continue
            row = {
                "symbol":     sym_u,
                "last_price": quote.get("last") or quote.get("last_price"),
                "net_change": quote.get("change") or quote.get("net_change"),
                "volume":     quote.get("volume"),
                "bid_price":  quote.get("bid") or quote.get("bid_price"),
                "ask_price":  quote.get("ask") or quote.get("ask_price"),
            }
            with self._lock:
                tracker = self._vwap_data.get(sym_u)
                vwap = tracker.vwap if (tracker and tracker.day_key == today) else 0.0
            sig = self._build_signal_from_row(row, cfg, vwap=vwap)
            if sig is None:
                continue
            seen.add(sym_u)
            candidates.append(sig)

        # 3) Persist all signals (accepted + rejected) for diagnostics
        with self._lock:
            self._signals.extend(candidates)
            if len(self._signals) > SIGNAL_LOG_LIMIT:
                self._signals = self._signals[-SIGNAL_LOG_LIMIT:]
        self._mark_dirty()

        # 4) Pick top non-rejected
        accepted = sorted(
            (s for s in candidates if not s.rejected),
            key=lambda s: s.score, reverse=True,
        )
        if not accepted:
            return

        # 5) Pre-subscribe the top few candidates so the tick-driven path
        #    can fire immediately on the next price update.
        top_syms = [s.symbol for s in accepted[: cfg.max_concurrent + 2]]
        try:
            self._hooks.subscribe_symbols(top_syms)
        except Exception:
            pass

        # 6) Enter best — acquire entry lock so a concurrent tick-driven entry
        #    cannot race us while we are evaluating.
        if not self._entry_lock.acquire(blocking=False):
            return   # tick-driven path already placing an order
        try:
            if not self._can_take_new_entry():
                return
            self._enter_position(accepted[0])
        finally:
            self._entry_lock.release()

    def _enter_position(self, sig: Signal) -> None:
        if not self._hooks:
            return
        cfg = self._cfg

        # ── Pre-entry hard gates (broker truth) ─────────────────
        # Even though scanner already filtered with broker_has_any_today,
        # broker state can change between scan and entry; recheck.
        if self._hooks.broker_has_any_today is not None:
            try:
                if self._hooks.broker_has_any_today(sig.symbol):
                    self._emit(
                        "warn",
                        "skip entry: broker has activity on symbol today",
                        {"sym": sig.symbol},
                    )
                    return
            except Exception:
                pass
        if self._hooks.broker_market_open is not None:
            try:
                if not self._hooks.broker_market_open():
                    self._emit("warn", "skip entry: market not OPEN", {"sym": sig.symbol})
                    return
            except Exception:
                pass

        # ── Cash + sizing ───────────────────────────────────────
        try:
            cash = float(self._hooks.get_cash() or 0.0)
        except Exception:
            cash = 0.0
        if cash <= 0:
            self._emit("warn", "no cash available, skipping entry", {"sym": sig.symbol})
            return

        # ── Position sizing: risk-based Kelly (single master formula) ──
        #
        # How many shares to buy so that if the stop triggers,
        # we lose exactly risk_per_trade_pct % of available cash?
        #
        #   qty = floor( (cash × risk_pct/100) / (price × stop_pct/100) )
        #
        # One hard safety rail after: never commit more than 95% of cash.
        # PKR caps (max_buy_amount_per_trade, daily_max_buy_amount) applied below.
        risk_amount    = cash * (cfg.risk_per_trade_pct / 100.0)
        per_share_risk = sig.last_price * (cfg.stop_pct / 100.0)
        if per_share_risk <= 0:
            return
        qty = math.floor(risk_amount / per_share_risk)

        # Hard ceiling: never spend more than 95% of cash on one trade.
        qty = min(qty, math.floor(cash * 0.95 / sig.last_price))

        qty = max(0, qty)
        if qty <= 0:
            self._emit("warn", "qty=0 after sizing", {
                "sym":             sig.symbol,
                "cash":            cash,
                "price":           sig.last_price,
                "risk_amount":     round(risk_amount, 2),
                "per_share_risk":  round(per_share_risk, 4),
            })
            return

        # ── Liquidity cap: don't queue more than 3× ask depth ───
        try:
            quote = self._hooks.get_quote(sig.symbol) or {}
        except Exception:
            quote = {}
        ask_vol_raw = (
            quote.get("ask_vol")
            or quote.get("ask_volume")
            or quote.get("askVolume")
            or 0
        )
        try:
            ask_vol = int(float(ask_vol_raw or 0))
        except (TypeError, ValueError):
            ask_vol = 0
        if ask_vol <= 0:
            # Fall back to 1% of today's traded volume as a soft cap.
            ask_vol = max(1, int(sig.volume * 0.01))
        if qty > ask_vol * 3:
            new_qty = max(1, ask_vol)
            self._emit("info", "qty capped by ask depth", {
                "sym": sig.symbol, "wanted": qty, "ask_vol": ask_vol, "qty": new_qty,
            })
            qty = new_qty

        # Buy at ask + offset for fill
        entry_price = round(sig.ask * (1 + cfg.entry_offset_pct / 100.0), 2)
        # Safety cap: max buy amount per single trade.
        if cfg.max_buy_amount_per_trade > 0 and entry_price > 0:
            max_qty_trade = math.floor(cfg.max_buy_amount_per_trade / entry_price)
            qty = min(qty, max_qty_trade)
        if qty <= 0:
            self._emit("warn", "blocked by max_buy_amount_per_trade", {
                "sym": sig.symbol, "entry_price": entry_price,
                "max_buy_amount_per_trade": cfg.max_buy_amount_per_trade,
            })
            return
        # Safety cap: total daily buy amount.
        buy_value = round(entry_price * qty, 4)
        if cfg.daily_max_buy_amount > 0:
            remaining = cfg.daily_max_buy_amount - self._daily_buy_value
            if remaining <= 0:
                self._emit("warn", "blocked by daily_max_buy_amount", {
                    "sym": sig.symbol,
                    "daily_buy_value": self._daily_buy_value,
                    "daily_max_buy_amount": cfg.daily_max_buy_amount,
                })
                return
            max_qty_daily = math.floor(remaining / max(entry_price, 0.0001))
            qty = min(qty, max_qty_daily)
            if qty <= 0:
                self._emit("warn", "blocked by daily_max_buy_amount", {
                    "sym": sig.symbol,
                    "daily_buy_value": self._daily_buy_value,
                    "daily_max_buy_amount": cfg.daily_max_buy_amount,
                })
                return
            buy_value = round(entry_price * qty, 4)
        target = round(entry_price * (1 + cfg.target_pct / 100.0), 2)
        stop   = round(entry_price * (1 - cfg.stop_pct / 100.0), 2)

        pos = Position(
            pos_id=str(uuid.uuid4())[:8],
            symbol=sig.symbol,
            side="buy",
            qty=qty,
            entry_price=entry_price,
            target_price=target,
            stop_price=stop,
            status="pending_entry",
        )
        try:
            ord_hash = self._hooks.place_limit_buy(sig.symbol, entry_price, qty)
        except Exception as e:
            self._emit("error", f"place_limit_buy failed for {sig.symbol}", {"err": str(e)})
            return
        pos.entry_ord_hash = ord_hash

        # Subscribe symbol so we get live ticks (idempotent)
        try:
            self._hooks.subscribe_symbols([sig.symbol])
        except Exception:
            pass

        with self._lock:
            self._positions[pos.pos_id] = pos
            self._ord_to_pos[ord_hash] = pos.pos_id
            self._trade_count_today += 1
        self._emit("info", f"ENTRY sent {sig.symbol}", {
            "pos_id": pos.pos_id, "qty": qty, "price": entry_price,
            "target": target, "stop": stop, "ord_hash": ord_hash,
        })
        self._mark_dirty()

    def _maybe_place_slo(self, pos: Position) -> None:
        cfg = self._cfg
        if not (cfg.use_slo and self._hooks and self._hooks.place_slo):
            return
        try:
            slo_hash = self._hooks.place_slo(
                pos.symbol, pos.qty,
                pos.stop_price * 1.001,  # trigger slightly above stop
                pos.stop_price,
            )
            with self._lock:
                pos.slo_ord_hash = slo_hash
                pos.updated_ts = time.time()
            self._emit("info", f"SLO placed {pos.symbol}", {"pos_id": pos.pos_id, "ord_hash": slo_hash})
            self._mark_dirty()
        except Exception as e:
            self._emit("warn", f"SLO place failed {pos.symbol}", {"err": str(e)})

    # ── Position management ─────────────────────────────────────

    def _latest_price(self, symbol: str) -> float:
        if not self._hooks:
            return 0.0
        try:
            q = self._hooks.get_quote(symbol)
            if not q:
                return 0.0
            return float(q.get("last") or q.get("last_price") or 0.0)
        except Exception:
            return 0.0

    def _manage_open_positions(self) -> None:
        with self._lock:
            holdings = [p for p in self._positions.values() if p.status == "holding"]
        for pos in holdings:
            last = self._latest_price(pos.symbol)
            if last <= 0:
                continue

            # Trailing stop?
            cfg = self._cfg
            if cfg.trailing_stop_pct > 0:
                trail = round(last * (1 - cfg.trailing_stop_pct / 100.0), 2)
                if trail > pos.stop_price:
                    with self._lock:
                        pos.stop_price = trail
                        pos.updated_ts = time.time()
                    self._mark_dirty()

            # Target hit
            if last >= pos.target_price:
                self._exit_position(pos, "target")
                continue
            # Stop hit
            if last <= pos.stop_price:
                self._exit_position(pos, "stop")
                continue

    def _enforce_force_exit(self) -> None:
        if self._now_hhmm() < self._cfg.force_exit_hhmm:
            return
        with self._lock:
            holdings = [p for p in self._positions.values() if p.status == "holding"]
            pending_entries = [
                p for p in self._positions.values()
                if p.status == "pending_entry" and p.entry_ord_hash
            ]
        # Close holdings via market sell (urgent).
        for pos in holdings:
            self._exit_position(pos, "time_cutoff")
        # Cancel pending entries that never filled — release the working
        # capital and don't leave dangling orders into next session.
        for pos in pending_entries:
            try:
                self._hooks.cancel_order(pos.entry_ord_hash)
            except Exception as e:
                self._emit("warn", "cancel pending_entry failed at cutoff", {
                    "pos_id": pos.pos_id, "err": str(e),
                })
                continue
            self._emit("info", "cancel pending_entry past cutoff", {
                "pos_id": pos.pos_id, "symbol": pos.symbol,
            })

    def _exit_position(self, pos: Position, reason: str) -> None:
        if not self._hooks:
            return
        cfg = self._cfg
        # Determine qty for this sell order subject to safety caps.
        last = self._latest_price(pos.symbol)
        est_px = last if last > 0 else (pos.entry_price or 0.0)
        sell_qty = pos.qty
        if est_px > 0 and cfg.max_sell_amount_per_trade > 0:
            cap_qty = math.floor(cfg.max_sell_amount_per_trade / est_px)
            sell_qty = min(sell_qty, cap_qty)
        if est_px > 0 and cfg.daily_max_sell_amount > 0:
            remaining_sell = cfg.daily_max_sell_amount - self._daily_sell_value
            cap_qty_day = math.floor(max(remaining_sell, 0.0) / est_px)
            sell_qty = min(sell_qty, cap_qty_day)
        if sell_qty <= 0:
            self._emit("warn", "blocked by sell safety caps", {
                "pos_id": pos.pos_id,
                "symbol": pos.symbol,
                "qty": pos.qty,
                "est_px": est_px,
                "max_sell_amount_per_trade": cfg.max_sell_amount_per_trade,
                "daily_sell_value": self._daily_sell_value,
                "daily_max_sell_amount": cfg.daily_max_sell_amount,
            })
            return
        with self._lock:
            if pos.status != "holding":
                return
            pos.status = "pending_exit"
            pos.exit_reason = reason
            pos.exit_order_qty = sell_qty
            pos.updated_ts = time.time()

        # ── 1. Cancel SLO FIRST — must not fire concurrently with manual exit
        slo_cancelled_ok = True
        if pos.slo_ord_hash:
            try:
                slo_cancelled_ok = bool(self._hooks.cancel_order(pos.slo_ord_hash))
            except Exception as e:
                slo_cancelled_ok = False
                self._emit("warn", "SLO cancel exception", {
                    "pos_id": pos.pos_id, "err": str(e),
                })
            if not slo_cancelled_ok:
                # Broker IDs may not be cached yet; mark for retry by the loop.
                with self._lock:
                    pos.slo_cancel_pending_ts = time.time()
                self._emit("warn", "SLO cancel returned False, will retry", {
                    "pos_id": pos.pos_id, "slo_ord_hash": pos.slo_ord_hash,
                })

        try:
            if reason in ("time_cutoff", "stop") or last <= 0:
                ord_hash = self._hooks.place_market_sell(pos.symbol, sell_qty)
            else:
                price = round(last * (1 - cfg.exit_offset_pct / 100.0), 2)
                if price <= 0:
                    price = pos.target_price
                ord_hash = self._hooks.place_limit_sell(pos.symbol, price, sell_qty)
        except Exception as e:
            with self._lock:
                pos.status = "holding"   # rollback
                pos.exit_reason = ""
                pos.exit_order_qty = 0
            self._emit("error", f"exit order failed {pos.symbol}", {"err": str(e)})
            return

        with self._lock:
            pos.exit_ord_hash = ord_hash
            pos.exit_filled_qty = 0
            pos.exit_avg_price = 0.0
            self._ord_to_pos[ord_hash] = pos.pos_id
        self._emit("info", f"EXIT sent {pos.symbol}", {
            "pos_id": pos.pos_id, "reason": reason, "ord_hash": ord_hash,
        })
        self._mark_dirty()

    def _retry_slo_cancels(self) -> None:
        """Retry SLO cancels that returned False because broker IDs weren't yet cached."""
        if not self._hooks:
            return
        now = time.time()
        with self._lock:
            todo = [
                p for p in self._positions.values()
                if p.slo_ord_hash
                and p.slo_cancel_pending_ts
                and (now - p.slo_cancel_pending_ts) >= 2.0
                and p.slo_cancel_attempts < 3
            ]
        for pos in todo:
            try:
                ok = bool(self._hooks.cancel_order(pos.slo_ord_hash))
            except Exception:
                ok = False
            with self._lock:
                pos.slo_cancel_attempts += 1
                if ok:
                    pos.slo_ord_hash = ""
                    pos.slo_cancel_pending_ts = 0.0
                    self._emit("info", "SLO cancel retry succeeded", {"pos_id": pos.pos_id})
                else:
                    pos.slo_cancel_pending_ts = now
                    if pos.slo_cancel_attempts >= 3:
                        self._emit("error", "SLO cancel retries exhausted", {
                            "pos_id": pos.pos_id, "slo_ord_hash": pos.slo_ord_hash,
                        })
            self._mark_dirty()

    def _finalize_exit(self, pos: Position, fill_price: float) -> None:
        with self._lock:
            if fill_price > 0:
                pos.exit_price = fill_price
            order_qty = pos.exit_order_qty if pos.exit_order_qty > 0 else pos.qty
            qty = pos.exit_filled_qty if pos.exit_filled_qty > 0 else order_qty
            # Add this exit batch's P/L to any previously-realized partials
            if pos.exit_price > 0 and pos.entry_price > 0 and qty > 0:
                pos.realized_pl = round(
                    pos.realized_pl + (pos.exit_price - pos.entry_price) * qty, 2,
                )
            # Reduce remaining holding qty by what this exit order sold.
            pos.qty = max(0, pos.qty - qty)
            pos.exit_order_qty = 0
            pos.exit_filled_qty = 0
            pos.exit_avg_price = 0.0
            pos.exit_ord_hash = ""
            pos.status = "closed" if pos.qty <= 0 else "holding"
            pos.updated_ts = time.time()
            if pos.realized_pl < 0:
                self._last_loss_ts = time.time()
        self._emit("info", f"EXIT filled {pos.symbol}", {
            "pos_id": pos.pos_id,
            "exit_price": pos.exit_price,
            "realized_pl": pos.realized_pl,
            "remaining_qty": pos.qty,
            "reason": pos.exit_reason,
        })
        if pos.status == "closed":
            self._archive_position(pos)
        else:
            self._mark_dirty()

    # ── Bot vs broker reconciliation ────────────────────────────

    def _reconcile_with_broker(self) -> None:
        """
        Periodic check that broker still confirms our 'holding' positions.
        If the broker no longer shows a position we think we hold (e.g.
        the user manually sold it in the dashboard, or a SLO triggered
        outside our knowledge), mark it closed with reason 'broker_drift'
        so it's surfaced in the events log for investigation.
        """
        if not self._hooks or self._hooks.get_broker_positions is None:
            return
        try:
            rows = self._hooks.get_broker_positions() or []
        except Exception as e:
            log.warning(f"reconcile fetch failed: {e}")
            return
        broker_pos = {}
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            if not sym:
                continue
            try:
                qty = int(float(r.get("net_position") or r.get("net_qty") or 0))
            except (TypeError, ValueError):
                qty = 0
            broker_pos[sym] = qty

        drifted: list[Position] = []
        with self._lock:
            for p in self._positions.values():
                if p.status != "holding":
                    continue
                bp_qty = broker_pos.get(p.symbol, 0)
                if bp_qty <= 0:
                    drifted.append(p)
        for pos in drifted:
            with self._lock:
                pos.status = "closed"
                pos.exit_reason = "broker_drift"
                pos.updated_ts = time.time()
            self._emit("warn", "broker no longer holds position", {
                "pos_id": pos.pos_id, "symbol": pos.symbol,
            })
            self._archive_position(pos)

    def _archive_position(self, pos: Position) -> None:
        with self._lock:
            self._closed_trades.append(asdict(pos))
            if len(self._closed_trades) > TRADE_LOG_LIMIT:
                self._closed_trades = self._closed_trades[-TRADE_LOG_LIMIT:]
        self._mark_dirty()

    # ── Views ───────────────────────────────────────────────────

    def _position_view(self, pos: Position, last: float) -> dict:
        d = asdict(pos)
        if last > 0:
            d["last_price"] = last
            d["mtm_pl"] = round(pos.mtm_pl(last), 2)
            d["mtm_pl_pct"] = round(pos.mtm_pl_pct(last), 3)
        return d


# ══════════════════════════════════════════════════════════════════
# MODULE-LEVEL SINGLETON
# ══════════════════════════════════════════════════════════════════

_singleton: Optional[DailyTrader] = None
_singleton_lock = threading.Lock()


def get() -> DailyTrader:
    """Return the module-level DailyTrader (lazy init)."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = DailyTrader()
    return _singleton

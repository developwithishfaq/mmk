# Daily Trader

**Last updated:** 2026-05-10
**Related:** [market_rules.md](market_rules.md), [circuit_limits.md](circuit_limits.md), [order_flow.md](order_flow.md), [broker_api.md](broker_api.md)

Intraday momentum bot. File: `daily_trader.py` (root). Hooks wired in
`mmk_backend/services/daily_trader_hooks.py`.

---

## State Machine

```
STOPPED ──start()──► RUNNING ──halt()──► HALTED
   ▲                    │                   │
   └──────reset()───────┘◄──────resume()────┘
```

| State | Meaning |
|-------|---------|
| `STOPPED` | Bot idle; no scanning, no position management |
| `RUNNING` | Actively scanning and managing positions |
| `HALTED` | Paused (e.g. daily loss limit hit); positions still managed |

State is **never** auto-resumed to `RUNNING` on restart — deserialized as `STOPPED`.

---

## Key Classes

### `Config` (dataclass, all fields have defaults)

Strategy parameters. Can be patched at runtime via `PATCH /daily-trader/config`.

**Time gates** (PKT, UTC+5):

| Field | Default | Meaning |
|-------|---------|---------|
| `entry_start_hhmm` | `"09:35"` | No entries before this (3 min after open for price discovery) |
| `entry_stop_hhmm` | `"14:00"` | No new entries after 2 PM |
| `force_exit_hhmm` | `"15:15"` | Force-close all positions 15 min before close |

**Position sizing:**

| Field | Default | Meaning |
|-------|---------|---------|
| `risk_per_trade_pct` | `2.0` | % of available cash at risk per trade |
| `stop_pct` | `1.0` | Stop distance from entry (%) — denominator in position size formula |
| `max_buy_amount_per_trade` | `1000` | Hard cap per trade (PKR) |
| `daily_max_buy_amount` | `10000` | Hard cap total for the day (PKR) |

**Target / exits:**

| Field | Default | Meaning |
|-------|---------|---------|
| `target_pct` | `2.0` | Take-profit target (%) |
| `stop_pct` | `1.0` | Stop-loss (%) |
| `trailing_stop_pct` | `0.0` | Trailing stop (0 = disabled) |

**Risk controls:**

| Field | Default | Meaning |
|-------|---------|---------|
| `max_concurrent` | `3` | Max open positions at once |
| `max_orders_per_day` | `6` | Max entries placed in a session |
| `daily_loss_limit_pct` | `3.0` | Halt bot when daily P/L drops below -3% |
| `cooldown_after_loss_s` | `300` | Seconds to pause after a losing trade |

**Scanning:**

| Field | Default | Meaning |
|-------|---------|---------|
| `scan_interval_sec` | `10` | Poll scanner cadence |
| `tick_driven` | `True` | Also trigger entry eval on every MF price tick |
| `use_vwap_filter` | `True` | Reject entries if price is too far above VWAP |
| `vwap_max_above_pct` | `0.5` | Max % price can be above VWAP |

---

### `Hooks` (dataclass, dependency injection)

All broker I/O goes through this bundle, making the bot testable without a live connection.

| Hook | Signature | Source |
|------|-----------|--------|
| `get_quote` | `(symbol) → dict` | Live tick from `runtime.prices` |
| `subscribe_symbols` | `(symbols: list[str])` | Socket subscription |
| `fetch_feed` | `() → list[dict]` | Full price cache snapshot |
| `get_cash` | `() → float` | `get_available_cash()` → `pCashAmt` |
| `place_limit_buy` | `(symbol, qty, price) → str` | Returns `ord_hash` |
| `place_limit_sell` | `(symbol, qty, price, ord_hash) → str` | Attaches to parent position |
| `place_market_sell` | `(symbol, qty, ord_hash) → str` | Force exit |
| `cancel_order` | `(ord_hash) → bool` | Requires pm report received first |
| `place_slo` | `(symbol, qty, stop_price, ord_hash) → str` | MF socket |
| `broker_has_any_today` | `() → bool` | Broker order history check |
| `broker_market_open` | `() → bool` | `ht` socket status, REST fallback |
| `get_broker_positions` | `() → list[dict]` | `getclientexposure` |
| `broker_refresh` | `()` | Clears cached exposure |

Installed via `daily_trader_hooks.install_hooks()` after login.

---

### `Position` (dataclass)

Per-trade state. Lifecycle: `pending_entry → holding → pending_exit → closed`.

Important fields:
- `filled_qty` / `avg_fill_price` — entry VWAP (updated on partial fills)
- `exit_filled_qty` / `exit_avg_price` — exit VWAP
- `slo_ord_hash` — tracks the stop-loss order placed on MF socket
- `broker_drift` — set true if broker snapshot shows position not in local state

---

### `VwapTracker`

Per-symbol intraday VWAP approximation from cumulative `Σ(last × volume_delta)` / `Σ(volume_delta)`.
Reset daily (new session).

Called on every `on_tick()` update. Used in signal scoring and filtering.

---

## Loop Structure (`_loop`, runs in background thread)

Every monitor tick (default 1 s):

```
1. _manage_open_positions()     # check exits, SLO state
2. _retry_slo_cancels()         # retry failed SLO cancels (up to 3×)
3. _reconcile_with_broker()     # every 60 s — broker position sync
4. _scan_and_enter()            # every scan_interval_sec (10 s)
5. _enforce_force_exit()        # after force_exit_hhmm, closes everything
```

Tick-driven mode additionally calls `on_tick(symbol, tick)` per MF message,
throttled to **1 eval per 2 s per symbol** via a timestamp cache.

---

## Entry Signal Scoring

```python
score = chg_pct × (log10(volume) / 5) × vwap_bonus
vwap_bonus = max(0.2, 1.0 - pct_above_vwap × 0.1)
```

- `chg_pct` — % change from previous close
- Volume term normalises score across different float sizes
- `vwap_bonus` penalises entries when price is running above VWAP

**Rejected if:**
- `use_vwap_filter=True` and `(last - vwap)/vwap × 100 > vwap_max_above_pct`
- Price outside circuit limits (checked in `place_limit_buy`)
- Market not open, time gates, concurrent cap, daily order cap, daily loss limit

---

## Position Sizing Formula

```python
qty = floor(cash × risk_pct/100 / (price × stop_pct/100))
# Then cap at:
qty = min(qty, floor(cash × 0.95 / price))       # 95% cash ceiling
qty = min(qty, floor(max_buy_amount / price))     # per-trade PKR cap
qty = min(qty, floor(remaining_daily / price))    # daily PKR cap
qty = min(qty, ask_depth_vol × 3)                 # don't overwhelm ask
```

---

## Persistence

File: `daily_trader_state.json` (repo root)

- Atomic write: write to `.tmp` then `os.replace()` — never corrupted
- Debounced 1 s background saver thread
- Loaded on `DailyTrader.__init__()`
- **`state` is always reset to `STOPPED` on load** — manual restart required

---

## API Endpoints

All under `/daily-trader`:

| Method | Path | Action |
|--------|------|--------|
| GET | `/status` | Full state snapshot |
| POST | `/start` | Start → RUNNING |
| POST | `/stop` | Stop → STOPPED |
| POST | `/halt` | Halt → HALTED |
| POST | `/reset-day` | Clear daily stats, positions |
| GET | `/config` | Current Config as JSON |
| PATCH | `/config` | Update Config fields live |
| GET | `/signals` | Last scanner output |
| GET | `/positions` | Open positions |
| GET | `/trades` | Completed trades |
| GET | `/events` | Event log |
| POST | `/close` | Force-close a position by symbol |
| POST | `/sync` | Manual broker reconciliation |
| GET | `/broker-snapshot` | Raw broker position list |
| GET | `/report` | Win rate, gross P/L, avg winner/loser |

---

## Hooks Wiring — `daily_trader_hooks.py`

`install_hooks()` must be called after every login (session changes).

Key implementation notes:
- `BrokerView` singleton stored in `runtime.broker_view`, lazy-init with double-checked locking
- `dt_broker_market_open()` prefers `runtime.market_status` (live socket `ht`), REST fallback
- `_check_cap_limits(symbol, price, side)` — raises `ValueError` if price outside bands
  - Key: `f"01_{symbol.upper()}"` (REG market)
  - Silently passes if limits not yet loaded
- Cancel requires `exch_order_id` (tag 37) + `house_order_id` (tag 41) in `runtime.orders`
  - These only arrive after the `pm` FIX execution report — cancel will fail if called too early

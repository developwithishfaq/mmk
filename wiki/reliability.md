# Reliability & Order-State Truth

**Last updated:** 2026-05-29
**Related:** [daily_trader.md](daily_trader.md), [order_flow.md](order_flow.md), [socket_messages.md](socket_messages.md), [broker_api.md](broker_api.md), [known_issues.md](known_issues.md)

How the bot detects order status, the fragilities it inherits from socket-only flows,
and the safety rails added on 2026-05-29.

---

## Sources of Order-Status Truth

The broker pushes order state on two socket message types and exposes the same
state via two polling REST endpoints. Each has different latency / reliability
characteristics:

| Source | Type | Latency | Reliability | Used for |
|--------|------|---------|-------------|----------|
| **`pm`** FIX execution report | Socket | <500 ms | Drops on socket disconnect | Primary fill / reject signal → `bot.on_order_update()` |
| **`or`** broker ack | Socket | <500 ms | Same | Fast accept/reject (used pre-2026-05-29 only for logging) |
| `/api_new/getactivitylogs` | REST | 0–10 s (TTL) | Authoritative | Pre-trade gate, *should be* the reconciler source-of-truth |
| `/api_new/getOutstandingOrders` | REST | 0–5 s (TTL) | Authoritative | Live working orders |
| `/api_new/getopenposition` | REST | 0–15 s (TTL) | Authoritative | Position drift check in reconciler |
| `/api_new/getClientTrade` | REST | On demand | Authoritative | Consolidated net-position-by-symbol — manual audit |

All REST endpoints are wrapped in [broker_view.py](../broker_view.py) with per-endpoint TTLs.

---

## The Fragility (pre-2026-05-29 baseline)

The bot's `pending_entry → holding` and `pending_exit → closed` transitions ran **only** on
the `pm` socket path:

1. `place_limit_buy` returns `ord_hash` and the bot enters `pending_entry`.
2. The bot waits for a `pm` execution report with FIX tag 39 = `"2"` (filled).
3. `on_order_update(ord_hash, "filled", ...)` is called and the position becomes `holding`.

If step 3's `pm` never arrives — session displaced, socket dropped, broker re-routes the
client_order_id, etc. — the bot is stuck in `pending_entry` forever even though the broker
already filled the order. This was observed on 2026-05-29 with MUGHAL, SSGC, FFC after a
session displacement.

The `_reconcile_with_broker()` loop ran every 60 s but only checked **drift on holdings**
(broker no longer shows a position) — it did not consult the activity log to repair
stuck `pending_entry` or `pending_exit` orders.

---

## Two PM Routing Bugs Patched (2026-05-29)

Both in [`mmk_backend/services/socket_handler.py`](../mmk_backend/services/socket_handler.py).

### Bug 1 — PM matching never resolved DT orders

The broker rewrites our md5 `ordHash` (FIX tag 11 / client_order_id) to its own short
alias (e.g. `0O4GA0F1`) in the `pm` execution report. The strict-match path
(`o.get("client_order_id") == client_ord_id or h == client_ord_id`) therefore failed
for daily-trader orders, and the symbol+side fallback explicitly excluded DT orders
(`if o.get("daily_trader"): continue`).

**Fix:** the fallback now does two passes — non-DT orders first (preserves prior
behaviour for manual / auto-trade orders), then DT orders. When a DT order matches by
symbol + side + status ∈ {sent, delivered, new}, the broker's `client_order_id` is
cached on the runtime row so subsequent PMs for the same order match strictly.

### Bug 2 — OR rejections never reached the bot

`or` messages from the broker server with `success=false` were only logged. The bot's
`pending_entry` row stayed alive forever for SSGC/FFC rejected for insufficient cash.

**Fix:** on OR reject, look up the order's symbol in `runtime.orders`, update its status
to `"rejected"`, and if `ord_hash` is in `bot._ord_to_pos` call
`bot.on_order_update(ord_hash, "rejected", symbol=...)`.

---

## Reconciler Safety Rule (2026-05-29)

`_reconcile_with_broker()` (in `daily_trader.py`) used to mark every `holding` position
as `broker_drift` whenever its symbol was missing from the broker positions list.
On a fresh server start, the broker_view positions cache is empty for ~15 seconds —
during that window the reconciler would silently close every holding the bot had.

**Fix:** if `get_broker_positions()` returns an empty list, the reconciler treats it
as "I don't know" and bails out. Drift detection requires positive evidence (a
non-empty rows list where the symbol is absent).

This addresses the "MUGHAL marked broker_drift after restart" failure seen on 2026-05-29.

---

## Reconciler-by-Activity-Log (plumbing in, logic pending)

The hooks plumbing for an authoritative reconciler is wired:

- `BrokerView.activity_today_all()` returns the full TTL-cached activity log
- `dt_get_activity_logs()` in `daily_trader_hooks.py` exposes it as a hook
- `Hooks.get_activity_logs: Optional[Callable[[], list[dict]]]`
- `install_hooks()` includes it

What it would do once the logic is added to `_reconcile_with_broker()`:

For every `pending_entry` / `pending_exit` position, find the activity-log row matching
by `(symbol, side, ordered_qty)` and most-recent `order_time`. If the row shows
`status="filled"` or `"partial_fill"`, call `bot.on_order_update(ord_hash, status,
fill_price=row.order_price, fill_qty=row.filled_qty, cum_qty=row.filled_qty,
avg_px=row.order_price)`. If `"rejected"` or `"cancelled"`, call the rejected branch.

`on_order_update()` is idempotent (uses `max(0, x - old_filled)` deltas), so re-calling
it after the socket already updated state causes no double-counting.

This was **not yet committed** as of 2026-05-29. The hook exists but is not consumed.

---

## Session Displacement Pattern

Broker enforces **one active session per user_id**. The broker app/website and the bot
share the same `RF991` credentials. Whenever you open the broker app while the bot is
running:

1. Broker disconnects our PM and ES/MP/MO/BR/MF WebSockets with `ds` (session displaced)
2. REST calls silently return `{success: false}` or `{available_cash: null}` — they do
   not raise
3. `get_cash()` reads 0 → every signal logs `"no cash available, skipping entry"`
4. In-flight PM execution reports are lost forever

Recovery: stop trader, POST `/auth/login` with `.env.local` creds (server-side helper),
POST `/daily-trader/sync` to repopulate broker_view, start trader.

Operators should keep the broker app **closed** while the bot is running. No automatic
recovery is wired — by design (would compete with the human session).

---

## Sizing Reliability — In-Flight Tracking (2026-05-29)

Previous sizing fed today's bug: with cash=Rs 1,716 and risk_pct=1%, the bot fired three
back-to-back orders all sized against the same pre-MUGHAL cash value. MUGHAL filled first
and consumed Rs 1,612; SSGC and FFC were rejected by the broker for insufficient cash.

The 30-second TTL on `get_available_cash` couldn't catch this — by the time the broker's
cash field updated, the rejections had already been sent.

**Fix in `_enter_position()`** — sizing now subtracts in-flight commitment before the
per-slot split:

```python
in_flight_commit = sum(p.entry_price * p.qty
                       for p in self._positions.values()
                       if p.status == "pending_entry")
available_cash   = max(0.0, cash - in_flight_commit)
slots_remaining  = max(1, cfg.max_concurrent - slots_used)
per_slot_budget  = available_cash / slots_remaining
qty_kelly        = floor(available_cash * risk%/100 / (price * stop%/100))
qty_budget       = floor(per_slot_budget / price)
qty              = min(qty_kelly, qty_budget)  # then PKR caps below
```

Two new Config fields:

- `min_trade_notional` (default Rs 300) — skip entry if per-slot budget falls below this
- `min_cap_room_pct` (default 1.0 %) — reject signals within this distance of the upper
  circuit limit (uses new `Hooks.get_upper_cap_room_pct`)

The previous "95 % of cash" rail is gone — the per-slot budget subsumes it.

---

## Quick Operator Checks

When the bot looks stuck or stats look wrong, in order:

1. `curl /auth/me` — `authenticated: false` ⇒ session lost
2. `curl /account/cash` — `null` ⇒ session lost
3. `curl /daily-trader/broker-snapshot` — `cash_ts: 0` ⇒ never refreshed; `positions_count: 0` with stale `positions_ts` ⇒ broker rejecting REST
4. `curl /orders/activitylogs` — compare to `bot.open_positions[].status` to spot
   stuck `pending_entry` / `pending_exit`
5. `curl /orders/consolidated?symbol=X` — authoritative net buy/sell for symbol X

If a position is stuck `pending_entry` and the activity log shows it filled, the
reconciler-by-activity-log gap is the cause. Manual fix: stop trader, edit
`daily_trader_state.json`, kill uvicorn worker so the lifespan-shutdown persist doesn't
clobber the edit, restart.

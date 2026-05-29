# Activity Log

Append-only chronological record of all wiki operations.

---

## [2026-05-10] ingest | Initial wiki population from project codebase + APK reverse engineering

Created all 9 wiki pages from knowledge accumulated during development sessions:
authentication flow, all REST endpoints, every socket message type, FIX protocol tags,
order placement/cancellation payloads, PSX market rules, circuit limits, DailyTrader
architecture, and a known-issues page capturing non-obvious broker behaviour.
Sources: `mmk_api.py`, `socket_handler.py`, `daily_trader.py`, `broker_view.py`,
decompiled APK (`munirkhanani/` Java source), live API probing sessions.

## [2026-05-29] ingest | Order-status reliability + sizing rewrite + signal improvements

Live session uncovered a cascade of issues after a session displacement (broker app
opened mid-trade) and produced four code changes plus a new wiki page.

Code patched:
- `mmk_backend/services/socket_handler.py` — PM fallback now matches DT orders by
  symbol+side+pending status (broker rewrites client_order_id to a short alias, so the
  strict match never worked for daily-trader orders); OR rejections now route to
  `bot.on_order_update(ord_hash, "rejected", ...)`.
- `daily_trader.py` — sizing rewrite: per-slot cash allocation across remaining
  concurrent slots + in-flight commitment tracking + `min_trade_notional` floor.
  Score formula gained spread penalty + bid/ask book-imbalance bonus. New
  `near_upper_cap` rejection (uses cap_limits cache via new hook). `_reconcile_with_broker`
  now bails on empty broker positions data (refuses to act on absence of evidence).
- `mmk_backend/services/daily_trader_hooks.py` — added `dt_get_activity_logs` and
  `dt_get_upper_cap_room_pct` hook implementations; wired into `install_hooks()`.
- `broker_view.py` — added `activity_today_all()` for unfiltered access to the cached
  activity log (was previously only available filtered by symbol).

Config gained two fields: `min_trade_notional` (default Rs 300) and `min_cap_room_pct`
(default 1.0 %). Both safe to default for existing state files (Config.from_dict drops
unknown keys and fills missing ones from defaults).

Created `reliability.md` documenting all four sources of order-status truth, the
fragilities (socket-only fill detection, session displacement, broker session
exclusivity), the two PM/OR routing bugs and their fixes, the reconciler safety rule,
the in-flight-tracked sizing rewrite, and an "activity-log reconciler" gap that has
plumbing in place but no consumer logic yet.

Updated `daily_trader.md` to reflect the new sizing formula, scoring formula, and
expanded `Hooks` table. Added cross-link to `reliability.md`. Updated `index.md` and
this log.

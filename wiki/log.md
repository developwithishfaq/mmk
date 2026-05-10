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

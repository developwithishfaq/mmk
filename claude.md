# MMK Trading Bot — Project Rules

## Wiki First

**Before writing any code or answering any question about the broker API, market rules, order
flow, or bot architecture — read the relevant wiki page(s) first.**

```
wiki/index.md          ← start here, find the right page
wiki/broker_api.md     ← all REST endpoints + response shapes
wiki/socket_messages.md← every WebSocket message type
wiki/fix_protocol.md   ← FIX tag reference, order status codes
wiki/order_flow.md     ← place / cancel / modify payloads, ordHash
wiki/market_rules.md   ← PSX hours, session phases, order restrictions
wiki/circuit_limits.md ← upper/lower price bands per symbol
wiki/authentication.md ← login flow, session tokens, socket setup
wiki/daily_trader.md   ← DailyTrader state machine, Config, VWAP
wiki/known_issues.md   ← bugs fixed, gotchas, things that will bite you
```

After answering or making changes, update the relevant wiki page if new facts were discovered.
Append an entry to `wiki/log.md`.

---

## Project Layout

```
server.py                        ← FastAPI app entry point (uvicorn)
daily_trader.py                  ← Trading bot (DailyTrader, Config, Hooks, VwapTracker)
mmk_api.py                       ← All broker REST API calls
mmk_backend/
  runtime_state.py               ← Shared mutable state singleton (runtime)
  fix_parser.py                  ← FIX protocol string parser
  services/
    auth_service.py              ← Login, session persistence, cap limits fetch
    socket_handler.py            ← WebSocket message handler (mf, ht, pm, or, ...)
    daily_trader_hooks.py        ← Wires DailyTrader.Hooks to live broker calls
    broker_view.py               ← Cached REST market status / positions
    auto_trade_service.py        ← Simpler auto-trade (non-DailyTrader)
  routers/                       ← FastAPI route handlers
  settings.py                    ← App settings (paths, CORS)
wiki/                            ← LLM knowledge base (see above)
daily_trader_state.json          ← Bot state persistence (auto-written)
```

---

## Running the Server

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Server must be started manually — all autostart scripts have been removed.

---

## Key Architecture Facts

- **`runtime`** is a module-level singleton (`mmk_backend.runtime_state.runtime`). Import it directly — do not instantiate `RuntimeState`.
- **Credentials** are read from env vars `MMK_USER_ID`, `MMK_PASSWORD`, `MMK_PIN` at import time. Set them before starting the server.
- **Market status** comes from the `ht` WebSocket message → `runtime.market_status`. Do not poll REST for this.
- **Available cash** comes from `getclientexposure → aBuyPowerAd.pCashAmt`. Never use `getavailablecash` (withdrawal-only endpoint).
- **Cancel orders** require tag 37 + tag 41 from the `pm` FIX report — not from the `or` ack.
- **SLO orders** use the MF socket, not the PM socket. Cancel SLOs on MF socket too.
- **`ordHash`** = MD5 of `"HH:MM:SS.microseconds"`. Generate a fresh one for every request.

---

## Coding Conventions

- Python 3.11+, type hints on all function signatures
- `log = logging.getLogger(__name__)` in every module
- Guard all `runtime.*_lock` access with `with` statements
- Raise `ValueError` for validation failures (price limits, bad params)
- Background threads: always `daemon=True`, always named
- No bare `except:` — catch specific exceptions or at minimum `Exception as e`
- Atomic file writes: write to `.tmp` then `os.replace()`

---

## Do Not

- Do not re-add autostart scripts or a connection watchdog (intentionally removed)
- Do not use `getavailablecash` REST endpoint
- Do not reuse an `ordHash` across requests
- Do not call cancel before the `pm` report has populated `exch_order_id` / `house_order_id`
- Do not hardcode credentials — use env vars

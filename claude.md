# MMK Trading Bot — Project Rules

## How This Project Is Operated

**This project is controlled by Claude Code through chat, not by the user clicking a web UI.**
That is the user's explicit preference. In every session in this repo, treat yourself as the
operations console for the bot.

You run the show. The user will say things like *"start the server"*, *"start the trader"*,
*"what's the status"*, *"any trades today"*, *"stop trader"*, *"tail the logs"*, *"change target
to 4%"* — and you execute them via the FastAPI endpoints below (server is on
`http://127.0.0.1:8000`). Don't redirect them to the web UI. Don't ask them to run uvicorn
themselves. Don't ask them to paste credentials — they live in `.env.local` (gitignored) and
`scripts/serve.ps1` loads them.

**Read-only — just run, then summarize compactly:**
`/daily-trader/status`, `/positions`, `/trades`, `/signals`, `/events`, `/account/*`, `/market/*`,
log tailing (last 10–20 lines of `logs/trader.log`).

**State-changing — confirm with the user before calling:**
- Starting/stopping the server (`.\scripts\serve.ps1`, killing uvicorn)
- `POST /daily-trader/start` / `/stop` / `/halt` / `/reset-day` / `/close`
- `PATCH /daily-trader/config` (any config change while live)
- `POST /order/place` / `/cancel` / `/change` / `/place-slo`
- Any `/account/withdrawal*` endpoint

**Response style:** compact state + key numbers by default. Raw JSON only on request. Tail logs
in short slices, never dump the whole file. If you don't know whether the server is up, probe
`/daily-trader/status` before assuming.

---

## Wiki First

**Before writing any code or answering any question about the broker API, market rules, order
flow, or bot architecture — consult the wiki first.**

Start at `wiki/index.md` to find the relevant page(s), then read them before proceeding.

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

Preferred (loads credentials from `.env.local`):
```powershell
.\scripts\serve.ps1
```

Manual fallback (env vars must already be set in the shell):
```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Server must be started manually (no autostart). You — Claude — start it on request, in the
background, then verify with `GET /daily-trader/status`.

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

# Known Issues & Gotchas

**Last updated:** 2026-05-10
**Related:** [broker_api.md](broker_api.md), [order_flow.md](order_flow.md), [circuit_limits.md](circuit_limits.md)

Non-obvious broker behaviours, bugs found and fixed, and things that will bite you again.

---

## Broker API Gotchas

### 1. `getavailablecash` is a WITHDRAWAL endpoint — do not use for balance

**Symptom:** Returns `{ "msg": "Cash Withdrawal not allowed at this time" }` during market hours.
The endpoint is time-restricted and returns no numeric data.

**Fix:** Use `getclientexposure` instead. Read `aData.aBuyPowerAd.pCashAmt` for tradeable cash.
See `mmk_api.get_available_cash()`.

**Impact:** UI showed "Available Cash N/A" for the entire trading session until fixed.

---

### 2. Cancel order requires `pm` report — not just `or`

To cancel, you need **tag 37** (`exch_order_id`) and **tag 41** (`house_order_id`).
These only arrive in the **pm** FIX execution report, **not** in the `or` broker ack.

**Symptom:** Cancel silently fails if called immediately after `or` arrives. The `pm` message
can arrive 200 ms–2 s later than `or`.

**Fix:** `daily_trader_hooks.dt_cancel_order()` checks `runtime.orders[ord_hash]` for these
fields before calling cancel. If they're missing, it returns `False` — the retry loop in
`_retry_slo_cancels()` handles the backoff.

---

### 3. SLO (Stop-Loss Orders) use MF socket, not PM socket

Regular orders: **PM socket** (`pushOrder`)
Stop-loss orders: **MF socket** (`pushSLO`)

Cancel routing is also different:
- Regular cancel → **PM socket**
- SLO cancel → **MF socket** (identified by `ord_hash.endswith("SL")`)

Mixing sockets causes silent failure — the broker receives nothing.

---

### 4. `ordHash` is NOT a random UUID

`ordHash` = MD5 of `"HH:MM:SS.microseconds"` string.

**You must generate a new hash for every single request** (place, cancel, modify).
Re-using the same hash causes the exchange to deduplicate and silently drop the second order.

```python
import hashlib
from datetime import datetime
def get_ord_hash() -> str:
    now = datetime.now()
    return hashlib.md5(
        (now.strftime("%H:%M:%S.") + f"{now.microsecond:06d}").encode()
    ).hexdigest()
```

---

### 5. Circuit limit fetch returns empty response on some sessions

**Symptom:** Log shows `"Cap limits fetch failed: Expecting value: line 1 column 1 (char 0)"`
The `GET /api_new/getCapLock?v=1` endpoint occasionally returns an empty body.

**Behaviour:** Non-blocking background fetch — logged as warning only. The bot proceeds
without limits. `_check_cap_limits()` silently passes orders when `cap_limits` is empty.

**Workaround:** Manual refresh mid-session:
```python
from mmk_backend.services.auth_service import _refresh_cap_limits_async
_refresh_cap_limits_async(runtime.session)
```

---

### 6. Market status must come from socket `ht`, not REST polling

The REST endpoint `getmarketstatus` (via `BrokerView`) has a **30-second TTL cache**.
Using it for order gating means you can attempt trades up to 30 s after market close.

**Fix:** `runtime.market_status` is updated instantly from the `ht` WebSocket message.
`dt_broker_market_open()` reads the socket state first, REST only as fallback.

---

### 7. `getmarketstatus` REST vs socket status strings differ

REST returns `"OPEN"` or `"CLOSE"` (or similar broker-specific codes).
Socket `ht` message returns `"OPENED"`, `"CLOSED"`, `"OHO"`, `"HALT"`, etc.

`_OPEN_STATUSES = {"OPENED", "OPEN", "OHO"}` covers both sources.
Do not assume any single string — always check against the set.

---

### 8. Bid/ask volumes were missing from the original market feed parser

The original `_parse_price_row()` did not extract indices `[4]` (bid_vol) and `[7]` (ask_vol)
from the `mf` array. Position sizing uses ask depth to avoid overwhelming the book.

**Fix:** Added `bid_vol` and `ask_vol` to `CSV_FIELDS` and the parser. The `mf` array
must have at least 17 elements for any fields to be populated (`len(row) < 17 → return None`).

---

### 9. `getclientexposure` key names are inconsistent

The `aBuyPowerAd` sub-object uses `p`-prefixed camelCase keys (`pCashAmt`, `pTotCash`).
Other sub-objects in the same response use different conventions.

Always guard with `isinstance(bp, dict)` before reading fields — the key is absent (not null)
if the account has no buying power data.

---

### 10. HTTP order mode also requires a PM socket `ordKey` message

When placing via HTTP (`op != "socket"`), the server does not automatically associate your
`ordHash` with the resulting `pm` execution reports.

After the HTTP POST, you must also send on the PM socket:
```
[9, {"key": "ordKey", "val": "<ordHash>"}]
```

Without this, you will never receive the `pm` report and cannot cancel or track the order.

---

### 11. `RuntimeState` credentials read from env at import time

`runtime_state.py` reads `MMK_USER_ID`, `MMK_PASSWORD`, `MMK_PIN` **at module import time**.
Setting these env vars after the module is imported has no effect.

Hardcoded fallback defaults are present in the file — remove these before any deployment
outside the local machine.

---

### 12. `daily_trader_state.json` state resets to STOPPED on load

The `DailyTrader` deserializer intentionally forces `state = STOPPED` regardless of what was
saved. This prevents the bot from auto-starting after a crash or restart.

If you need to persist RUNNING state across restarts, change `_load_state()` in `daily_trader.py`.
Currently this is a safety feature, not a bug.

---

## Fixed Bugs (historical)

| Date | Issue | Fix |
|------|-------|-----|
| 2026-05-10 | `get_available_cash()` returned null during market hours | Switched from `getavailablecash` to `getclientexposure.aBuyPowerAd.pCashAmt` |
| 2026-05-10 | Market status had 30 s lag from REST polling | Added `ht` socket handler → `runtime.market_status` updated instantly |
| 2026-05-10 | Bid/ask volumes missing from price feed | Added indices `[4]` and `[7]` to `_parse_price_row()` |
| 2026-05-10 | Circuit limits not fetched on login | Added `_refresh_cap_limits_async()` called from `try_auto_login_from_disk()` |
| 2026-05-10 | Auto-reconnect watchdog caused restart loops | Removed `watchdog.py` and all VBScript/Task Scheduler autostart scripts entirely |

---

## Things That May Break After Broker API Changes

- **`aHeader` column order in `getCapLock`** — parsed by column name, not index; robust to reordering but breaks if header names change
- **`mf` array indices** — hardcoded integer indices; a broker update adding/removing array positions will silently corrupt all price data
- **FIX tag mapping** — if broker changes tag 37/41 semantics, cancel logic breaks silently
- **Socket message type strings** — `"ht"`, `"hf"`, `"pm"`, `"or"` etc are string literals; no schema validation

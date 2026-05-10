# PSX Market Rules

**Last updated:** 2026-05-10
**Related:** [circuit_limits.md](circuit_limits.md), [socket_messages.md](socket_messages.md), [daily_trader.md](daily_trader.md)

Pakistan Stock Exchange trading hours, session phases, and order type restrictions.

---

## Trading Hours (PKT, UTC+5)

| Time | Event |
|------|-------|
| ~09:15 | Pre-open session begins |
| ~09:32 | Market opens (OPENED) |
| ~15:30 | Market closes (CLOSED) |

Bot time gates (configurable in `Config`):
- `entry_start_hhmm = "09:35"` — wait 3 min after open for price discovery to settle
- `entry_stop_hhmm  = "14:00"` — no new entries after 2 PM
- `force_exit_hhmm  = "15:15"` — close all positions 15 min before close

---

## Market Status Phases

Received via **ht** socket message. `runtime.market_status` is updated instantly.

| Status | Meaning | Orders allowed |
|--------|---------|---------------|
| `OPENED` | Normal trading | All order types |
| `OHO` | Off-Hour Orders | Limit only, no short sell, no odd lots |
| `PRE-OPEN` | Pre-open phase | Limit only |
| `PRE-CLOSE` | Pre-close | Limit only, be cautious |
| `HALT` | Trading halted | No orders |
| `SUSPENDED` | Symbol suspended | No orders |
| `CLOSED` | After hours | No orders |
| `LOADING` | System loading | No orders |
| `DUMPED` | Dumped state | No orders |

Our `_OPEN_STATUSES = {"OPENED", "OPEN", "OHO"}` — these allow trading.

---

## Market Codes

| Code | Market |
|------|--------|
| `01` | REG (Regular equity market) |
| `FUT` | Futures market |
| `ODL` | Odd Lot |

---

## Order Types by Session

| Session | Market Orders | Limit Orders | Short Sell | Leverage Buy |
|---------|--------------|-------------|-----------|-------------|
| OPENED | ✅ | ✅ | ✅ (FUT only) | ✅ (REG, if CotStatus allows) |
| OHO | ❌ | ✅ | ❌ | ❌ |
| PRE-OPEN | ❌ | ✅ | ❌ | ❌ |
| CLOSED | ❌ | ❌ | ❌ | ❌ |

---

## Leverage / CotStatus

Each symbol has a `cotStatus` field from the symbol list:

| CotStatus | Meaning |
|-----------|---------|
| `MTS` | Both leverage (G-side) and regular trading allowed |
| `BOTH` | Same as MTS |
| `MFS` | Converts G-side to regular buy |
| `NO` or empty | No leverage, regular trading only |

---

## Circuit Breakers

PSX enforces intraday price bands. Default upper limit is approximately **+7.5%** from previous close. Lower limit applies symmetrically.

Exact per-symbol limits fetched via `GET /api_new/getCapLock?v=1` on login.
See [circuit_limits.md](circuit_limits.md) for details.

---

## Settlement

- Equity trades settle T+2 (trade date + 2 business days)
- CDC (Central Depository Company) holds securities

---

## Trading Days

Monday–Friday. No trading on Pakistani public holidays. Market opens at the times above on all normal trading days.

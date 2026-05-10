# FIX Protocol Reference

**Last updated:** 2026-05-10
**Related:** [socket_messages.md](socket_messages.md), [order_flow.md](order_flow.md)

FIX tag reference for the broker's **pm** execution report messages.

---

## Tag Reference

| Tag | Field name in broker | Our name | Notes |
|-----|---------------------|---------|-------|
| 1 | ApplID / ClientCode | client_code | Account code in order payload |
| 6 | AvgPx | avg_px | Average fill price |
| 11 | HouseOrderID | client_order_id | Our MD5 ordHash alias |
| 14 | CumQty | cum_qty | Cumulative filled qty |
| 17 | TicketNo | — | Exchange ticket number |
| 31 | LastPx | fill_price | Last fill price |
| 32 | LastQty | fill_qty | This-fill quantity |
| 37 | OrderID | exch_order_id | Exchange order ID — use for cancel |
| 38 | OrderQty | — | Original ordered quantity |
| 39 | OrdStatus | status | Status code (see below) |
| 40 | OrdType | order_type | `"1"`=Market, `"2"`=Limit |
| 41 | OrigClOrdID | house_order_id | Original house order ID — use for cancel |
| 44 | Price | — | Order price |
| 54 | Side | side_num | `"1"`=Buy, `"2"`=Sell, `"5"`/`"Y"`=Short, `"G"`=Leverage |
| 55 | Symbol | symbol | Symbol code |
| 58 | Text | message | Human-readable reason/message |
| 59 | TimeInForce | — | `"0"`=DAY, `"6"`=GTC |
| 60 | TransactTime | order_time | Time of order event |
| 99 | StopPx | stop_price | Stop price for SLO orders |
| 102/103/373/751 | — | error_code | Error code variants |
| 111 | MaxFloor | disclosed_volume | Iceberg visible quantity |
| 126 | ExpireTime | — | GTC expire time |
| 143 | MarketType | market | `"01"`=REG, etc. |
| 150 | ExecType | exec_type | Execution type |
| 151 | LeavesQty | remaining_qty | Remaining open quantity |
| 434 | CxlRejResponseTo | — | `"1"`=cancel reject, `"2"`=change reject |
| 448 | PartyID / ClientCode | client_code | Client account code |

---

## Order Status Codes (tag 39)

| Code | Status | Our label |
|------|--------|----------|
| `"0"` | New | `new` |
| `"1"` | Partial Fill | `partial_fill` |
| `"2"` | Filled | `filled` |
| `"4"` | Cancelled | `cancelled` |
| `"8"` | Rejected | `rejected` |

---

## Execution State Machine

The broker concatenates `SIDE + EXEC_TYPE` (tag 54 + tag 150) to produce a compound state code:

| Code | Meaning |
|------|---------|
| `G0` | Buy queued (pending) |
| `G1`, `G2`, `GF` | Buy filled |
| `12` | Buy partial fill |
| `1F` | Buy full fill |
| `G4` | Buy cancelled |
| `G5` | Buy changed/modified |
| `G8` | Buy rejected |
| `GA` | Buy SLO accepted |
| `GM` | Buy off-hour order accepted |
| `50`, `Y0` | Sell queued |
| `51`, `52`, `2F`, `YF`, `5F` | Sell filled |
| `54`, `Y4` | Sell cancelled |
| `55`, `Y5` | Sell changed |
| `58`, `Y8` | Sell rejected |
| `5A` | Sell SLO accepted |
| `5M`, `YM` | Sell off-hour accepted |
| `CANORDREJ` | Cancel order rejected |
| `CHGORDREJ` | Change order rejected |

---

## Order Types (tag 40)

| Value | Type | Price required |
|-------|------|---------------|
| `"1"` | Market | No |
| `"2"` | Limit | Yes (tag 44) |

## Time In Force (tag 59)

| Value | Meaning |
|-------|---------|
| `"0"` | DAY (expires end of session) |
| `"6"` | GTC (Good Till Cancel — provide tag 126 ExpireTime) |

## Order Side (tag 54)

| Value | Meaning | Allowed markets |
|-------|---------|----------------|
| `"1"` | Buy | All |
| `"2"` | Sell | All |
| `"5"` or `"Y"` | Short Sell | FUT only |
| `"G"` | Leverage Buy | REG only (requires CotStatus == MTS or BOTH) |

---

## Cancel via FIX Tags

To cancel an order you need both:
- tag 37 (`exch_order_id`) — from the `pm` report
- tag 41 (`house_order_id`) — from the `pm` report

These are stored in `runtime.orders[ord_hash]` after we receive the **pm** message.

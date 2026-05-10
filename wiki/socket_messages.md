# Socket Messages

**Last updated:** 2026-05-10
**Related:** [authentication.md](authentication.md), [fix_protocol.md](fix_protocol.md), [order_flow.md](order_flow.md)

Every WebSocket message type sent or received by the broker.

---

## Message Format

Broker messages arrive as JSON arrays: `[TYPE, PAYLOAD]`
- Type `5` = subscribe, `6` = unsubscribe, `9` = command/push

Parsed message shape: `{ "t": "<type>", "d": <data> }`

---

## Incoming Message Types (broker → us)

### Trading

| Type | Description | `d` shape |
|------|-------------|---------|
| **od** | Order delivered to exchange | `ord_hash` string |
| **or** | Order accepted/rejected by broker server | `{ ordHash, success, msg }` |
| **pm** | FIX execution report (fills, rejects, cancels) | FIX string — parse with `fix_parser.parse_fix()` |
| **mr** | Market reject | `{ HOUSE_ORDER_ID, message }` |
| **cr** | Cancel order acknowledgement | raw data |
| **co** | Change order acknowledgement | `{ ... }` |
| **chgr** | Change order rejection | `{ SECURITY_SYMBOL, ERROR_CODE, msg }` |

### Market Feed

| Type | Description | `d` shape |
|------|-------------|---------|
| **mf** | Live market price tick | array or array-of-arrays (see below) |
| **fma** | Bulk market feed from memory | array-of-arrays |
| **fm** | Single symbol feed from memory (on-demand) | same as mf |
| **fn** | Feed not available for symbol | — |
| **mo** | Market-by-order (MBO) depth | `[symbol, market, [[bid_orders], [ask_orders]]]` |
| **mp** | Market-by-price (MBP) depth | `[symbol, market, [[bid_levels], [ask_levels]]]` |
| **tv** | Exchange volume data | array |

### Session & Status

| Type | Description | `d` shape |
|------|-------------|---------|
| **ht** | **Market status** (real-time open/close signal) | string: `"OPENED"`, `"CLOSED"`, `"HALT"`, `"PRE-OPEN"`, etc. |
| **hf** | Feed manager status | `"1"` = connected, other = disconnected |
| **hm** | Message server status (handshake) | string |
| **hb** | Heartbeat | ignored |
| **es** | Extended/exchange state | ignored |
| **st** | Server time | `{ time: <unix_epoch_ms> }` |
| **ds** | Force disconnect | reason string — triggers re-login |
| **bh** | Symbol CotStatus update | `{ Mode: "MTS"|"BOTH"|"MFS"|"NO" }` |
| **br** | Broker-level response | — |
| **uc** | Update client codes list | — |
| **ig** | Ignored/unknown | — |

---

## `mf` Array Index Map

The `d` field of **mf** / **fma** is a flat array:

| Index | Field | Notes |
|-------|-------|-------|
| `[0]` | `symbol` | e.g. `"OGDC"` |
| `[1]` | `market` | e.g. `"01"` (REG) |
| `[4]` | `bid_vol` | Best bid volume |
| `[5]` | `bid` | Best bid price |
| `[6]` | `ask` | Best ask price |
| `[7]` | `ask_vol` | Best ask volume |
| `[8]` | `last` | Last traded price |
| `[11]` | `prev_close` | Previous close price |
| `[14]` | `high` | Day high |
| `[15]` | `low` | Day low |
| `[16]` | `change` | Net change from prev close |
| `[17]` | `volume` | Total volume today |

For **buy** order price pre-fill → use `ask` (index 6).
For **sell** order price pre-fill → use `bid` (index 5).

---

## Market Depth Structure

### MBO (`mo`) — Market By Order
```
d[0] = symbol
d[1] = market
d[2][0] = buy orders:  each entry = [price, volume]
d[2][1] = sell orders: each entry = [price, volume]
```

### MBP (`mp`) — Market By Price
```
d[0] = symbol
d[1] = market
d[2][0] = buy price levels:  each entry = [?, volume, price]
d[2][1] = sell price levels: each entry = [?, volume, price]
```

---

## Outgoing Commands (us → broker)

### Subscriptions
```
[5, "{\"mf\":[\"SYMBOL\"]}"]          # subscribe single symbol
[5, "{\"mfa\":[\"SYM1\",\"SYM2\"]}"]  # subscribe multiple (bunch mode)
[6, "{\"mf\":[\"SYMBOL\"]}"]          # unsubscribe single
[6, "{\"mf\":\"removeVals\"}"]        # unsubscribe all
[5, "{\"mbo\":[\"MARKET_SYMBOL\"]}"]  # MBO depth (e.g. "01_OGDC")
[5, "{\"mbp\":[\"MARKET_SYMBOL\"]}"]  # MBP depth
[5, "{\"exSt\":[\"exSt\"]}"]          # exchange state (send after connect)
```

### Commands
```
[9, 1]                                # heartbeat/ping
[9, {"key": "getServerTime"}]         # server time (every 10 min on PM socket)
[9, {"key": "getMf", "val": ["SYM"]}] # on-demand last price → returns fm/fma
[9, {"key": "pushOrder", "val": {}}]  # place order (socket mode)
[9, {"key": "cancelOrder", "val": {}}]# cancel order
[9, {"key": "changeOrder", "val": {}}]# modify order
[9, {"key": "pushSLO", "val": {}}]    # place stop-loss order (on MF socket)
[9, {"key": "ordKey", "val": "HASH"}] # register ord_hash after HTTP order
```

---

## `ht` Open Statuses

Our code (`_OPEN_STATUSES` in `socket_handler.py`):
- `"OPENED"` — normal trading
- `"OPEN"` — alternative form
- `"OHO"` — Off-Hour Orders (limit only, no short sell)

All other values (`"CLOSED"`, `"HALT"`, `"PRE-OPEN"`, `"SUSPENDED"`, etc.) = not tradeable.

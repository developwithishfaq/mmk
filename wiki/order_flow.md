# Order Flow

**Last updated:** 2026-05-10
**Related:** [socket_messages.md](socket_messages.md), [fix_protocol.md](fix_protocol.md), [broker_api.md](broker_api.md)

Complete lifecycle of an order from placement to fill/cancel.

---

## ordHash Generation

Every order requires a unique `ordHash` (deduplication key):

```python
import hashlib
from datetime import datetime

def get_ord_hash() -> str:
    now = datetime.now()
    time_str = now.strftime("%H:%M:%S.") + f"{now.microsecond:06d}"
    return hashlib.md5(time_str.encode()).hexdigest()

def get_utc() -> int:
    return int(time.time())
```

**Important:** Not a random UUID — it is the MD5 of the current time with microseconds.
Generate a **new** hash for every order (place, cancel, change).

---

## Place Order — WebSocket Mode (when `op == "socket"`)

Send on **PM socket**: `[9, {"key": "pushOrder", "val": { ... }}]`

```json
{
  "1":   "CLIENT_CODE",
  "38":  "QTY",
  "40":  "ORDER_TYPE",
  "44":  "PRICE",
  "54":  "SIDE",
  "55":  "SYMBOL_CODE",
  "59":  "TIF",
  "65":  "",
  "99":  "STOP_PRICE",
  "111": "DISCLOSED_VOL",
  "126": "",
  "143": "MARKET_TYPE",
  "167": "",
  "200": "",
  "201": "",
  "202": "",
  "206": "",
  "7200": "",
  "hOrderSide": "BUY" or "SELL",
  "pin": "1234",
  "remarks": "order",
  "ordHash": "<md5_hash>",
  "utc": 1234567890
}
```

After placing via HTTP, also send on PM socket: `[9, {"key": "ordKey", "val": "<ordHash>"}]`

---

## Place Order — HTTP Mode (when `op != "socket"`)

POST `/api_new/pushorder` with same fields as above as form data.

---

## Cancel Order — WebSocket

Send on **PM socket** (or **MF socket** if ord_hash ends with `"SL"`):

`[9, {"key": "cancelOrder", "val": { ... }}]`

```json
{
  "37":  "EXCH_ORDER_ID",
  "41":  "HOUSE_ORDER_ID",
  "54":  "ORDER_SIDE",
  "55":  "SYMBOL_CODE",
  "143": "MARKET_TYPE",
  "448": "CLIENT_CODE",
  "pin": "1234",
  "ordHash": "<new_md5_hash>",
  "utc": 1234567890
}
```

**Requires tag 37 and tag 41** — only available after receiving the **pm** execution report.

---

## Modify Order — WebSocket

`[9, {"key": "changeOrder", "val": { ... }}]`

```json
{
  "newVol":   "NEW_QTY",
  "newPrice": "NEW_PRICE",
  "pin":      "1234",
  "account":  "CLIENT_CODE",
  "orderId":  "ORDER_ID",
  "ordHash":  "<new_md5_hash>",
  "utc":      1234567890
}
```

---

## Stop-Loss Order (SLO)

Send on **MF socket**: `[9, {"key": "pushSLO", "val": { ... }}]`

SLO order hashes end with `"SL"` — cancel SLOs on MF socket, not PM.

---

## Acknowledgement Sequence

After placing an order, messages arrive in this order:

```
1. [od]   Order delivered to exchange      → ordHash confirmed delivered
2. [or]   Broker ack                       → success=true/false, rejection msg
3. [pm]   FIX execution report             → status, fill price, fill qty
           (may arrive multiple times for partial fills)
```

---

## Order State in `runtime.orders`

Each order stored as:
```python
{
  "ord_hash":        "<md5>",
  "symbol":          "OGDC",
  "side":            "buy",
  "side_num":        "1",
  "exch_order_id":   "<tag 37>",   # populated from pm message
  "house_order_id":  "<tag 41>",   # populated from pm message
  "client_order_id": "<tag 11>",   # our ordHash
  "status":          "filled",
  "fill_price":      14.83,
  "fill_qty":        500,
  "daily_trader":    True/False,
  "created_ts":      1234567890.0,
  "last_update_ts":  1234567890.0,
}
```

---

## Iceberg / Disclosed Volume

Set tag `111` (`DisClosedVolume`) to a qty less than the total to create an iceberg order
(only `111` shares visible in the order book at a time).
Set to `""` or `"0"` for normal orders.

---

## Price Pre-fill Convention (from APK)

- **Buy** order → pre-fill price from `ask` (mf index `[6]`, best ask)
- **Sell** order → pre-fill price from `bid` (mf index `[5]`, best bid)

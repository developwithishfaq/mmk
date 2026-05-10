# Circuit-Breaker Price Limits

**Last updated:** 2026-05-10
**Related:** [market_rules.md](market_rules.md), [order_flow.md](order_flow.md), [broker_api.md](broker_api.md)

Exchange-enforced upper and lower price limits per symbol. Orders outside these bands are
silently rejected by the exchange.

---

## Fetching

```
GET /api_new/getCapLock?v=1
```

Called in a background thread on login via `auth_service._refresh_cap_limits_async()`.

### Response shape

```json
{
  "aData": {
    "aHeader": ["SYMBOL_CODE", "MARKET_CODE", "UVAL", "LVAL", ...],
    "aData": [
      ["OGDC", "01", 95.50, 83.50],
      ...
    ]
  }
}
```

---

## Storage

Stored in `runtime.cap_limits` (dict, protected by `runtime.cap_limits_lock`).

Key format: `"MARKET_SYMBOL"` e.g. `"01_OGDC"`

Value: `{ "upper": 95.50, "lower": 83.50 }`

---

## Validation

`daily_trader_hooks._check_cap_limits(symbol, price, side)` runs before every
`dt_place_limit_buy()` and `dt_place_limit_sell()` call.

Raises `ValueError` with message if price is outside limits:
```
Order price 96.00 exceeds upper circuit limit 95.50 for OGDC
```

---

## PSX Default Circuit

Approximate default limits (exact values come from the API):
- **Upper circuit:** ~+7.5% from previous close
- **Lower circuit:** ~-7.5% from previous close

Some illiquid or suspended symbols may have tighter bands.
Limits change daily as they are recalculated from the previous close price.

---

## When Limits Are Not Loaded

If `cap_limits` is empty (limits not fetched yet), `_check_cap_limits()` silently
passes the order through. The exchange will reject it if needed. This is safe — we
never block orders due to missing data.

---

## Refreshing

Currently only loaded once at login. To refresh mid-session:

```python
from mmk_backend.services.auth_service import _refresh_cap_limits_async
_refresh_cap_limits_async(runtime.session)
```

# Broker REST API

**Last updated:** 2026-05-10
**Related:** [authentication.md](authentication.md), [order_flow.md](order_flow.md), [circuit_limits.md](circuit_limits.md)

All REST endpoints for Munir Khanani Securities (Microlinks "Flair" platform).

---

## Base URL

```
https://tlc.munirkhanani.com
```

All requests require headers set by `mmk_api._headers(session)`:
- `trdlnks`: session token
- `rtk`: random token
- `Accept-Encoding: gzip`

---

## Endpoints

### Account & Cash

| Method | Path | Key params | Notes |
|--------|------|-----------|-------|
| POST | `/api_new/getclientexposure` | `account`, `marketType=REG` | Returns positions + buying power. **Use this for cash balance** via `aData.aBuyPowerAd.pCashAmt` |
| POST | `/api_new/getClientDetails` | `account` | Client name, NIC, CDC ID, contact info |
| GET  | `/api_new/getavailablecash` | — | **Cash withdrawal** endpoint, time-restricted. NOT for reading balance |
| POST | `/api_new/getavailablecash` | `account`, `pin` | Same as above — withdrawal only |

### Buying Power Fields (`aBuyPowerAd`)

| Field | Meaning |
|-------|---------|
| `pCashAmt` | Available cash for trading (PKR) |
| `pTotCash` | Total cash |
| `pCdcAmt` | CDC / collateral value |
| `pCashUtil` | Cash committed to open orders |
| `pColUtil` | Collateral committed to open orders |

### Orders

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api_new/pushorder` | Place order (HTTP mode, when `op != "socket"`) |
| POST | `/api_new/cancelorder` | Cancel order (HTTP mode) |
| POST | `/api_new/getOutstandingOrders` | Open/pending orders |
| POST | `/api_new/gettradelogs` | Filled orders today |
| POST | `/api_new/getactivitylogs` | All order activity today |

### Market Data

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api_new/getMktStatus` | Market open/close status (`aData.PSX_STATUS`) |
| GET  | `/api_new/getCapLock?v=1` | Circuit-breaker price limits for all symbols |
| POST | `/api_new/getuserpref` | User watchlist (key `oWatchSetting`) |
| POST | `/api_new/getIndicesSummary` | KSE-100, KSE-30 index data |
| POST | `/api_new/getTopMovers` | Gainers/losers by % and price |
| POST | `/api_new/a/getsymbollist` | Full symbol list with market codes |
| POST | `/api_new/a/gettopsymbols` | Top symbols |

### Positions & History

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api_new/getopenposition` | Broker open positions (authoritative) |
| POST | `/api_new/getClientTrade` | Trade history |
| POST | `/api_new/getClientExposureDetails` | Requires `symbol`, `mktType`, `mode` (E=executed/O=pending) |
| POST | `/api_new/getExposureSummaryCat` | Exposure summary by category |
| POST | `/api_new/getExposureCat` | Category-level exposure rows |

### Admin

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api_new/login` | Authenticate |
| POST | `/api_new/logout` | End session |
| GET  | `/api_new/getdt` | Server time check (used pre-login to validate clock) |
| POST | `/api_new/setLocalTime` | Time sync diff |
| GET  | `/api_new/getexst` | Exchange state snapshot |
| POST | `/api_new/getNotifications` | Push notification history |
| POST | `/api_new/getAppVersion` | App version check |

### Account Opening / Misc

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api_new/getAccountStatement` | Ledger, params: `fromDate`, `toDate`, `ledgerType` (dd-MM-yyyy) |
| POST | `/api_new/getpendingwithdrawals` | Pending withdrawal requests |
| POST | `/api_new/submitwithdrawal` | Submit withdrawal |
| POST | `/api_new/cancelwithdrawal` | Cancel withdrawal by serial no |

---

## Response Envelope

All endpoints return:
```json
{
  "success": true/false,
  "msg": "...",
  "aData": { ... },
  "execTime": "0.09",
  "dateTime": "09-05-2026 11:46:46 PM"
}
```

Always check `success` before reading `aData`.

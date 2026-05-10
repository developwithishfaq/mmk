# Authentication

**Last updated:** 2026-05-10
**Related:** [broker_api.md](broker_api.md), [socket_messages.md](socket_messages.md), [known_issues.md](known_issues.md)

Login, session management, socket setup, and forced re-login handling.

---

## Login Flow

### Step 1 — POST `/api_new/login`

```
uId              = USERNAME  (uppercase)
uPass            = PASSWORD
userApp          = "additional_data"
currentVersion   = "10.6"
playerId         = (OneSignal push player ID — can be any fixed string)
```

### Step 2 — Parse `aData` from response

Key fields extracted from `aData`:

| Field | Stored as | Purpose |
|-------|-----------|---------|
| `socketServerAddr` | `Constants.socketUrlGol` | WebSocket server IP |
| `scHash` | `Constants.socketHashkeyGol` | Socket URL auth token |
| `op` | `Constants.op` | `"socket"` = WS orders, else HTTP orders |
| `aLsnrPorts` | array | WebSocket ports (PM, MF, MBO, MBP) |
| `pingSec` | int | Heartbeat interval in seconds |
| `aClients` | array | Sub-account codes (null if single account) |

### Step 3 — Open 4 WebSocket connections

Socket URL pattern: `wss://SERVER_IP:PORT/?t=HASHKEY`

| Socket | Name | Purpose |
|--------|------|---------|
| PM | `socketIndexPM` | Execution reports, order placement, ping |
| MF | `socketIndexMF` | Market feed, SLO orders |
| MBO | `socketIndexMBO` | Market-by-order depth |
| MBP | `socketIndexMBP` | Market-by-price depth |

After connect, subscribe to exchange state: `[5,"{\"exSt\":[\"exSt\"]}"]`

### Step 4 — Heartbeat

Send on all sockets every `pingSec` seconds: `[9,1]`

Server time refresh every 10 minutes on PM socket: `[9,{"key":"getServerTime"}]`

---

## Session Tokens

All HTTP requests after login include:
- Header `trdlnks`: session token
- Header `rtk`: random token (refreshed each response)
- Cookie `SERVERID`: sticky session routing

---

## Forced Re-login

Triggers:
- **`ds`** socket message (force disconnect) — `d` field contains reason string
- HTTP response header `rstate` == `"5"`

Re-login procedure:
1. POST `/api_new/logout`
2. Re-login with stored credentials
3. Reconnect all 4 sockets
4. Re-subscribe all symbols

---

## Implementation Notes

- Our code: `mmk_api.login()`, `connect_all_sockets()`, `auth_service.try_auto_login_from_disk()`
- Credentials persisted in `web_session_state.json` for auto-restore on server restart
- The `op` field from login determines order routing — check it after every login
- Server enforces device clock must be within ±2 days of server time; mismatched clocks cause silent login failure

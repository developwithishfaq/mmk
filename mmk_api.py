"""
mmk_api.py — Munir Khanani Trading API
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import threading
import datetime
from dataclasses import dataclass, field

import requests
import websocket
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("mmk.api")


# ══════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════

BASE_URL    = "https://tlc.munirkhanani.com"
SERVER_ID   = "TLC04"
APP_VERSION = "10.6"
PLAYER_ID   = "fd0e0ca0-4368-4845-8ef7-15eed495466a"

ORDER_TYPE_LIMIT  = "2"
ORDER_TYPE_MARKET = "1"

TIF_DAY = "0"

SIDE_BUY  = "1"
SIDE_SELL = "2"

H_SIDE_BUY  = "BUY"
H_SIDE_SELL = "SELL"

MARKET_REG = "REG"

# Numeric side codes used by the Android app for cancel
# (derived from decompiled OutstandingLogAdapter switch block)
CANCEL_SIDE = {
    "BUY":        "1",
    "LBUY":       "G",
    "SELL":       "2",
    "LSELL":      "2",
    "SHORT SELL": "Y",
    "CROSS":      "8",
}


# ══════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════

@dataclass
class Session:
    user_id:        str
    user_unique_id: str
    user_type:      str
    seq_no:         str
    mkt_stat:       str
    ping_sec:       int
    op:             str
    socket_server:  str
    socket_hash:    str
    ports:          list[str]
    token:          str
    trdlnks:        str
    raw:            dict = field(repr=False)


@dataclass
class SocketSet:
    all: list
    pm:  object = None   # order placement
    mf:  object = None   # market feed


# ══════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════

def _md5_key() -> str:
    now = datetime.datetime.now()
    time_str = now.strftime("%H:%M:%S.") + f"{now.microsecond:06d}"
    return hashlib.md5(time_str.encode()).hexdigest().upper()


def _utc() -> str:
    return str(int(time.time()))


def _headers(session: Session = None) -> dict:
    h = {
        "Accept-Encoding": "gzip",
        "Connection":      "Keep-Alive",
        "Content-Type":    "application/x-www-form-urlencoded",
        "Cookie":          f"SERVERID={SERVER_ID}",
        "Host":            "tlc.munirkhanani.com",
        "User-Agent":      "Dalvik/2.1.0 (Linux; U; Android 9; SM-G955N Build/NRD90M.G955NKSU1AQDC)",
    }
    if session:
        h["rtk"]     = session.token
        h["trdlnks"] = session.trdlnks
    return h


# ══════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════

def login(user_id: str, password: str) -> Session:
    resp = requests.post(
        f"{BASE_URL}/api_new/login",
        headers=_headers(),
        data={
            "uId":            user_id,
            "uPass":          password,
            "userApp":        "a",
            "currentVersion": APP_VERSION,
            "playerId":       PLAYER_ID,
        },
        timeout=30,
        verify=False,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"Login failed: {body.get('msg', body)}")

    a = body["aData"]
    return Session(
        user_id        = a["userId"],
        user_unique_id = a["userUniqueId"],
        user_type      = a["userType"],
        seq_no         = a["seqNo"],
        mkt_stat       = a["mktStat"],
        ping_sec       = int(a["pingSec"]),
        op             = a["op"],
        socket_server  = a["socketServerAddr"],
        socket_hash    = a["scHash"],
        ports          = a["aLsnrPorts"],
        token          = resp.headers.get("rtk", ""),
        trdlnks        = resp.headers.get("trdlnks", ""),
        raw            = body,
    )


# ══════════════════════════════════════════════════════════════════
# WEBSOCKET
# ══════════════════════════════════════════════════════════════════

class TradingSocket:
    def __init__(self, server, port, socket_hash, token, name, ping_sec=25,
                 on_message=None):
        self.name       = name
        self.ping_sec   = ping_sec
        self._on_msg_cb = on_message
        self._stop_ping = threading.Event()
        self._ws        = None

        url = f"{server}:{port}/?t={socket_hash}"
        self._url = url.replace("http://", "ws://").replace("https://", "wss://")
        self._ws_headers = {
            "SESS_TOKEN": token,
            "SECPROTO":   "wamp",
            "Cookie":     f"SERVERID={SERVER_ID}",
        }

    def connect(self):
        print(f"[{self.name}] Connecting -> {self._url}")
        self._ws = websocket.WebSocketApp(
            self._url,
            header=self._ws_headers,
            subprotocols=["wamp"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        threading.Thread(target=self._ws.run_forever, daemon=True).start()

    def send(self, payload: str):
        if self._ws:
            self._ws.send(payload)

    def close(self):
        self._stop_ping.set()
        if self._ws:
            self._ws.close()

    def _on_open(self, ws):
        log.info(f"[{self.name}] Connected")
        self._stop_ping.clear()
        threading.Thread(target=self._ping_loop, daemon=True).start()

    def _on_message(self, ws, raw: str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        if self._on_msg_cb:
            self._on_msg_cb(self.name, raw, parsed)

    def _on_error(self, ws, error):
        log.error(f"[{self.name}] WebSocket error: {error}")

    def _on_close(self, ws, code, msg):
        self._stop_ping.set()
        log.warning(f"[{self.name}] WebSocket closed (code={code})")

    def _ping_loop(self):
        while not self._stop_ping.wait(self.ping_sec):
            self.send("[9,1]")


# ══════════════════════════════════════════════════════════════════
# CONNECT / DISCONNECT
# ══════════════════════════════════════════════════════════════════

def connect_all_sockets(session: Session, on_message=None,
                        wait_sec=3.0) -> SocketSet:
    log.info(f"Connecting sockets for user {session.user_id} (op={session.op})")
    sockets = []
    pm = mf = None

    for entry in session.ports:
        parts = entry.split("|")
        if len(parts) != 2:
            continue
        port, name = parts
        sock = TradingSocket(
            server      = session.socket_server,
            port        = port,
            socket_hash = session.socket_hash,
            token       = session.token,
            name        = name,
            ping_sec    = session.ping_sec,
            on_message  = on_message,
        )
        sock.connect()
        sockets.append(sock)
        if "PM" in name: pm = sock
        if "MF" in name: mf = sock

    time.sleep(wait_sec)
    return SocketSet(all=sockets, pm=pm, mf=mf)


def disconnect_all_sockets(socket_set: SocketSet):
    for sock in socket_set.all:
        sock.close()


# ══════════════════════════════════════════════════════════════════
# WATCHLIST
# ══════════════════════════════════════════════════════════════════

def get_watchlist(session: Session) -> list[str]:
    resp = requests.post(
        f"{BASE_URL}/api_new/getuserpref",
        headers=_headers(session),
        data={"key": "oWatchSetting"},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    raw_val = body["aData"][0]["PROPERTY_VALUE"]
    return [s for s in raw_val.split("|") if s.strip()]


def subscribe_watchlist(socket_set: SocketSet, symbols: list[str]) -> None:
    if not socket_set.mf:
        raise RuntimeError("No MF socket available")
    inner = ",".join(f'\\"{ s }\\"' for s in symbols)
    socket_set.mf.send('[5, "{\\"mfa\\":[' + inner + ']}"]')


# ══════════════════════════════════════════════════════════════════
# OUTSTANDING ORDERS  (queued / cancellable)
# ══════════════════════════════════════════════════════════════════

def get_outstanding_orders(
    session: Session,
    symbol:      str = "",
    market_type: str = "",
) -> list[dict]:
    """
    Fetch all pending (cancellable) orders from the exchange REST API.

    Each dict in the returned list contains at minimum:
      EXCH_ORDER_ID   — FIX tag 37  → pass as exch_order_id to cancel_order()
      HOUSE_ORDER_ID  — FIX tag 41  → pass as house_order_id to cancel_order()
      ORDER_SIDE      — numeric "1"/"2"/...
      HOUSE_ORDER_SIDE — human label "BUY"/"SELL"/...
      SECURITY_SYMBOL
      MARKET_TYPE
      CLIENT_CODE
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getOutstandingOrders",
        headers=_headers(session),
        data={
            "mktType":   market_type,
            "symbol":    symbol,
            "hOrdSides": "BUY,SELL,LBUY,LSELL,SHORT SELL",
            "account":   session.user_id,
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    return body.get("aData", [])


# ══════════════════════════════════════════════════════════════════
# TRADE LOGS (today's actual buy/sell executions)
# ══════════════════════════════════════════════════════════════════

def get_trade_logs(
    session: Session,
    symbol:      str = "",
    market_type: str = "",
) -> list[dict]:
    """
    Fetch today's trade/order logs (non-queued history) from REST API.

    API returns compact numeric keys in `aData` and column names in `aHeader`.
    This helper maps each row to readable keys using that header.
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/gettradelogs",
        headers=_headers(session),
        data={
            "mktType":   market_type,
            "symbol":    symbol,
            "hOrdSides": "BUY,SELL,LBUY,LSELL,SHORT SELL",
            "account":   session.user_id,
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []

    rows = body.get("aData", [])
    header = body.get("aHeader", [])
    if not header:
        return rows

    mapped_rows = []
    for row in rows:
        if not isinstance(row, dict):
            mapped_rows.append(row)
            continue
        mapped = {}
        for idx, col_name in enumerate(header, start=1):
            mapped[col_name] = row.get(str(idx))
        mapped_rows.append(mapped)
    return mapped_rows


# ══════════════════════════════════════════════════════════════════
# ACTIVITY LOGS (today's full order lifecycle activity)
# ══════════════════════════════════════════════════════════════════

_ORDER_STATUS_LABELS = {"0": "queued", "1": "partial_fill", "2": "filled",
                        "4": "cancelled", "8": "rejected"}

_SIDE_LABELS = {"1": "buy", "2": "sell"}


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _explicit_rest_failure(body: dict) -> bool:
    """
    Only treat broker JSON as failure when ``success`` is explicitly false-ish.
    Missing ``success`` must not discard ``aData`` (some payloads omit it on success).
    """
    succ = body.get("success")
    if succ is False:
        return True
    if succ is None:
        return False
    s = str(succ).strip().lower()
    return s in ("false", "0", "no")


def _normalize_cexpsum(raw: object) -> dict:
    """Broker may send cExpSum as a dict or occasionally as [{"p_led_bal": ...}, ...]."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                return item
    return {}


def _parse_activity_row(row: dict, header: list[str]) -> dict:
    """Map a single activity log row from numeric keys to a rich dict."""
    raw = {}
    for idx, col_name in enumerate(header, start=1):
        raw[col_name] = row.get(str(idx))

    order_status_code = raw.get("order_status", "")
    trans_status = raw.get("trans_status", "")
    status = _ORDER_STATUS_LABELS.get(str(order_status_code), f"unknown({order_status_code})")
    if trans_status == "F" and status != "filled":
        status = "filled"

    side_num = raw.get("order_side", "")
    side = raw.get("house_order_side", "").upper()
    if not side:
        side = _SIDE_LABELS.get(str(side_num), str(side_num))

    return {
        "symbol":           raw.get("symbol_code"),
        "side":             side,
        "status":           status,
        "order_price":      _safe_float(raw.get("order_price")),
        "ordered_qty":      _safe_int(raw.get("ordered_qty")),
        "filled_qty":       _safe_int(raw.get("filled_qty")),
        "remaining_qty":    _safe_int(raw.get("remaining_qty")),
        "order_time":       raw.get("order_time"),
        "house_order_id":   raw.get("house_order_id"),
        "exch_order_id":    raw.get("order_id"),
        "ticket_no":        raw.get("ticket_no"),
        "client_code":      raw.get("client_code"),
        "market":           raw.get("market_code"),
        "order_type":       raw.get("order_type"),
        "order_side_num":   str(side_num) if side_num else None,
        "order_status_code": str(order_status_code) if order_status_code else None,
        "trans_status":     trans_status if trans_status else None,
        "stop_price":       _safe_float(raw.get("order_stop_price")),
        "disclosed_volume": _safe_int(raw.get("disclosed_volume")),
    }


def get_activity_logs(
    session: Session,
    symbol:      str = "",
    market_type: str = "",
) -> list[dict]:
    """
    Fetch today's activity logs from REST API.

    Includes queued, cancelled, rejected, filled, and other order states.
    Each returned dict contains parsed fields:
      symbol, side, status (queued/filled/cancelled/rejected/partial_fill),
      order_price, ordered_qty, filled_qty, remaining_qty, order_time,
      house_order_id, exch_order_id, ticket_no, client_code, market.

    Linking queued → filled: match by house_order_id + exch_order_id pair.
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getactivitylogs",
        headers=_headers(session),
        data={
            "mktType":   market_type,
            "symbol":    symbol,
            "hOrdSides": "BUY,SELL,LBUY,LSELL,SHORT SELL",
            "account":   session.user_id,
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []

    rows = body.get("aData", [])
    header = body.get("aHeader", [])
    if not header:
        return rows

    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed.append(_parse_activity_row(row, header))
    return parsed


# ══════════════════════════════════════════════════════════════════
# LOGOUT
# ══════════════════════════════════════════════════════════════════

def logout(session: Session) -> dict:
    """Logout current session via REST API."""
    resp = requests.post(
        f"{BASE_URL}/api_new/logout",
        headers=_headers(session),
        timeout=30,
        verify=False,
    )
    body = resp.json()
    return body if isinstance(body, dict) else {"success": False, "raw": body}


# ══════════════════════════════════════════════════════════════════
# SYMBOL LIST
# ══════════════════════════════════════════════════════════════════

def get_symbol_list(session: Session) -> list[dict]:
    """
    Fetch and parse active REG symbol list for suggestions.

    Returns:
      [{ "symbol": "AABS", "name": "Al-Abbas Sugar", "sector": "..." }, ...]
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/a/getsymbollist",
        headers=_headers(session),
        data={"v": "1"},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []

    a_data = body.get("aData", {})
    rows = a_data.get("aData", [])
    items = a_data.get("aItems", [])
    sectors = a_data.get("aSectorList", [])
    if not isinstance(rows, list):
        return []

    companies = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 10:
            continue
        if row[0] != "REG" or row[9] != "Y":
            continue
        item_idx = row[2] if len(row) > 2 else None
        sector_idx = row[4] if len(row) > 4 else None
        name = items[item_idx] if isinstance(item_idx, int) and 0 <= item_idx < len(items) else ""
        sector = sectors[sector_idx] if isinstance(sector_idx, int) and 0 <= sector_idx < len(sectors) else ""
        companies.append({
            "symbol": str(row[1]),
            "name": str(name),
            "sector": str(sector),
        })
    return companies


# ══════════════════════════════════════════════════════════════════
# PLACE ORDERS
# ══════════════════════════════════════════════════════════════════

def _build_order(account, volume, order_type, price, side,
                 symbol, h_side, market_type, pin, md5) -> str:
    return json.dumps({
        "1": account, "38": volume, "40": order_type,
        "44": price.replace(",", ""), "54": side, "55": symbol,
        "59": TIF_DAY, "65": "", "99": "0", "111": "0",
        "126": "", "143": market_type, "167": "", "200": "",
        "201": "", "202": "", "206": "", "7200": "",
        "hOrderSide": h_side, "pin": pin,
        "remarks": "order", "ordHash": md5, "utc": _utc(),
    }, separators=(",", ":"))


def place_limit_buy(socket_set, session, symbol, price, volume, pin,
                    market_type=MARKET_REG) -> str:
    md5 = _md5_key()
    payload = _build_order(session.user_id, volume, ORDER_TYPE_LIMIT, price,
                           SIDE_BUY, symbol, H_SIDE_BUY, market_type, pin, md5)
    socket_set.pm.send('[9,{"key":"pushOrder","val":' + payload + "}]")
    log.info(f"[ORDER] LIMIT BUY  {symbol}  qty={volume}  price={price}  ordHash={md5}")
    return md5


def place_limit_sell(socket_set, session, symbol, price, volume, pin,
                     market_type=MARKET_REG) -> str:
    md5 = _md5_key()
    payload = _build_order(session.user_id, volume, ORDER_TYPE_LIMIT, price,
                           SIDE_SELL, symbol, H_SIDE_SELL, market_type, pin, md5)
    socket_set.pm.send('[9,{"key":"pushOrder","val":' + payload + "}]")
    log.info(f"[ORDER] LIMIT SELL  {symbol}  qty={volume}  price={price}  ordHash={md5}")
    return md5


def place_market_buy(socket_set, session, symbol, volume, pin,
                     market_type=MARKET_REG) -> str:
    md5 = _md5_key()
    payload = _build_order(session.user_id, volume, ORDER_TYPE_MARKET, "0",
                           SIDE_BUY, symbol, H_SIDE_BUY, market_type, pin, md5)
    socket_set.pm.send('[9,{"key":"pushOrder","val":' + payload + "}]")
    log.info(f"[ORDER] MARKET BUY  {symbol}  qty={volume}  ordHash={md5}")
    return md5


def place_market_sell(socket_set, session, symbol, volume, pin,
                      market_type=MARKET_REG) -> str:
    md5 = _md5_key()
    payload = _build_order(session.user_id, volume, ORDER_TYPE_MARKET, "0",
                           SIDE_SELL, symbol, H_SIDE_SELL, market_type, pin, md5)
    socket_set.pm.send('[9,{"key":"pushOrder","val":' + payload + "}]")
    log.info(f"[ORDER] MARKET SELL  {symbol}  qty={volume}  ordHash={md5}")
    return md5


# ══════════════════════════════════════════════════════════════════
# CANCEL ORDER
# ══════════════════════════════════════════════════════════════════

def cancel_order(
    socket_set,
    session,
    symbol:         str,
    exch_order_id:  str,   # EXCH_ORDER_ID  from getOutstandingOrders → FIX tag 37
    house_order_id: str,   # HOUSE_ORDER_ID from getOutstandingOrders → FIX tag 41
    order_side:     str,   # numeric side: "1"=BUY "2"=SELL "G"=LBUY "Y"=SHORT SELL
    market_type:    str,
    pin:            str,
) -> str:
    """
    Cancel a queued (unexecuted) order.

    Field mapping confirmed from decompiled OutstandingLogAdapter:
      tag 37  → EXCH_ORDER_ID   (exchange-assigned order ID)
      tag 41  → HOUSE_ORDER_ID  (broker house order ID)
      tag 54  → numeric side    (1/2/G/Y/8)
      tag 55  → symbol
      tag 143 → market type     (REG / FUT / …)
      tag 448 → client code     (your user_id / account number)

    Numeric side codes (from Android switch block):
      BUY        → "1"
      LBUY       → "G"
      SELL       → "2"
      LSELL      → "2"
      SHORT SELL → "Y"
      CROSS      → "8"

    Use CANCEL_SIDE dict to convert a human label if needed:
      order_side = CANCEL_SIDE["BUY"]  # → "1"
    """
    md5 = _md5_key()
    payload = json.dumps(
        {
            "37":      exch_order_id,     # exchange order ID
            "41":      house_order_id,    # house order ID  (NOT tag 11, NOT tag 1)
            "54":      order_side,        # numeric side
            "55":      symbol,
            "143":     market_type,
            "448":     session.user_id,   # client code     (NOT tag 1)
            "pin":     pin,
            "ordHash": md5,
            "utc":     _utc(),
        },
        separators=(",", ":"),
    )
    socket_set.pm.send('[9,{"key":"cancelOrder","val":' + payload + "}]")
    log.info(f"[ORDER] CANCEL  {symbol}  side={order_side}  exch={exch_order_id}  house={house_order_id}  ordHash={md5}")
    return md5


# ══════════════════════════════════════════════════════════════════
# CHANGE ORDER (amend price / volume of a queued order)
# ══════════════════════════════════════════════════════════════════

def change_order(
    socket_set,
    session,
    exch_order_id: str,   # EXCH_ORDER_ID from getOutstandingOrders
    new_price:     str,
    new_volume:    str,
    pin:           str,
) -> str:
    """
    Amend the price and/or volume of a queued (outstanding) order.

    Field mapping (from OutstandingLogAdapter decompiled source):
      orderId  → EXCH_ORDER_ID (the exchange order ID)
      newPrice → new limit price
      newVol   → new volume
      account  → client user_id
    """
    md5 = _md5_key()
    payload = json.dumps(
        {
            "newVol":   new_volume,
            "newPrice": new_price,
            "pin":      pin,
            "account":  session.user_id,
            "orderId":  exch_order_id,
            "ordHash":  md5,
            "utc":      _utc(),
        },
        separators=(",", ":"),
    )
    socket_set.pm.send('[9,{"key":"changeOrder","val":' + payload + "}]")
    return md5


# ══════════════════════════════════════════════════════════════════
# MBO / MBP DEPTH SUBSCRIPTIONS
# ══════════════════════════════════════════════════════════════════

def _mbo_key(symbol: str, market: str = "REG") -> str:
    return f"{market}_{symbol}"


def subscribe_mbo(socket_set, symbol: str, market: str = "REG") -> None:
    """Subscribe to MBO (market-by-order) depth feed for a symbol."""
    key = _mbo_key(symbol, market)
    socket_set.mf.send(f'[5,"{{\\"mbo\\":[\\"{ key }\\"]}}"  ]'.replace("  ]", "]"))


def unsubscribe_mbo(socket_set, symbol: str, market: str = "REG") -> None:
    """Unsubscribe from MBO depth feed."""
    key = _mbo_key(symbol, market)
    socket_set.mf.send(f'[6,"{{\\"mbo\\":[\\"{ key }\\"]}}"  ]'.replace("  ]", "]"))


def subscribe_mbp(socket_set, symbol: str, market: str = "REG") -> None:
    """Subscribe to MBP (market-by-price / order book depth) feed for a symbol."""
    key = _mbo_key(symbol, market)
    socket_set.mf.send(f'[5,"{{\\"mbp\\":[\\"{ key }\\"]}}"  ]'.replace("  ]", "]"))


def unsubscribe_mbp(socket_set, symbol: str, market: str = "REG") -> None:
    """Unsubscribe from MBP depth feed."""
    key = _mbo_key(symbol, market)
    socket_set.mf.send(f'[6,"{{\\"mbp\\":[\\"{ key }\\"]}}"  ]'.replace("  ]", "]"))


# ══════════════════════════════════════════════════════════════════
# CLIENT EXPOSURE (open positions + buying power + account summary)
# ══════════════════════════════════════════════════════════════════

def _get_client_exposure_raw(session: Session) -> dict:
    resp = requests.post(
        f"{BASE_URL}/api_new/getclientexposure",
        headers=_headers(session),
        data={"account": session.user_id},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not isinstance(body, dict):
        return {}
    if _explicit_rest_failure(body):
        return {}
    a_data = body.get("aData")
    return a_data if isinstance(a_data, dict) else {}


def get_open_positions(session: Session) -> list[dict]:
    """
    Fetch open/current positions.

    Returns list of dicts with keys:
      symbol, market, net_position, market_rate, open_avg_price, open_value,
      open_volume, inventory_holdings, inventory_avg_price, inventory_value,
      unrealized_mtm, realized_gain_loss, cur_avg_rate, cur_net_amount, cur_net_qty
    """
    data = _get_client_exposure_raw(session)
    rows = data.get("cExp", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        unrealized = _safe_float(r.get("UNREALIZED_MTM_AMOUNT")) or 0.0
        realized   = _safe_float(r.get("REALIZED_GAIN_LOSS")) or 0.0
        result.append({
            "symbol":               r.get("SYMBOL", ""),
            "market":               r.get("MARKET", ""),
            "net_position":         r.get("NET_POSITION"),
            "market_rate":          _safe_float(r.get("MARKET_RATE")),
            "open_avg_price":       _safe_float(r.get("OPEN_AVG_PRICE")),
            "open_value":           _safe_float(r.get("OPEN_VALUE")),
            "open_volume":          _safe_int(r.get("OPEN_VOLUME")),
            "inventory_avg_price":  _safe_float(r.get("INVENTORY_AVG_PRICE")),
            "inventory_value":      _safe_float(r.get("INVENTORY_AVG_VALUE")),
            "inventory_holdings":   _safe_int(r.get("INVENTORY_HOLDINGS")),
            "pending_avg_rate":     _safe_float(r.get("PENDING_AVG_RATE")),
            "pending_net_qty":      _safe_int(r.get("PENDING_NET_QTY")),
            "pending_net_amount":   _safe_float(r.get("PENDING_NET_AMT")),
            "unrealized_mtm":       unrealized,
            "realized_gain_loss":   realized,
            "total_mtm":            round(unrealized + realized, 4),
            "cur_avg_rate":         _safe_float(r.get("CUR_AVG_RATE")),
            "cur_net_amount":       _safe_float(r.get("CUR_NET_AMT")),
            "cur_net_qty":          _safe_int(r.get("CUR_NET_QTY")),
        })
    return result


def get_account_summary(session: Session) -> dict:
    """
    Fetch account financial summary.

    cExpSum is usually a flat dict; some responses wrap the same keys in a one-element list.
    """
    data = _get_client_exposure_raw(session)
    if not isinstance(data, dict):
        return {}
    r = _normalize_cexpsum(data.get("cExpSum"))

    details = data.get("accountDetails") or {}
    if not isinstance(details, dict):
        details = {}

    if not r and not details:
        return {}

    return {
        "ledger_balance":           _safe_float(r.get("p_led_bal")),
        "cdc_amount":               _safe_float(r.get("p_cdc_amt")),
        "cdc_amount_hc":            _safe_float(r.get("p_cdc_amt_hc")),
        "loan_amount":              _safe_float(r.get("p_loan_amt")),
        "cash_block":               _safe_float(r.get("p_cash_block")),
        "cash_withdrawal":          _safe_float(r.get("p_cash_withdrawal")),
        "held_amount":              _safe_float(r.get("p_held_amt")),
        "realized_pl":              _safe_float(r.get("p_realized_pl_amt")),
        "inventory_sold":           _safe_float(r.get("p_inventory_sold")),
        "unrealized_mtm_profit":    _safe_float(r.get("p_unreal_mtm_profit")),
        "unrealized_mtm_loss":      _safe_float(r.get("p_unreal_mtm_loss")),
        "net_worth":                _safe_float(r.get("p_net_worth")),
        "open_position":            _safe_float(r.get("p_open_pos")),
        "cash_margin_req":          _safe_float(r.get("p_cash_margin_req")),
        "collateral_margin_req":    _safe_float(r.get("p_col_margin_req")),
        "current_buy":              _safe_float(r.get("p_current_buy")),
        "current_sell":             _safe_float(r.get("p_current_sell")),
        "pending_buy":              _safe_float(r.get("p_pending_buy")),
        "pending_sell":             _safe_float(r.get("p_pending_sell")),
        "expense_amount":           _safe_float(r.get("p_expense_amount")),
        "inventory_realized_pl":    _safe_float(r.get("p_inventory_realized_pl")),
        "inventory_unrealized_pl":  _safe_float(r.get("p_inventory_unrealized_pl")),
        "account_percentage":       _safe_float(r.get("p_acc_per")),
        # account details
        "client_name":   details.get("p_client_name"),
        "dealer_code":   details.get("p_dealer_code"),
        "cdc_id":        details.get("p_cdc_id"),
        "email":         details.get("p_email_address"),
        "mobile":        details.get("p_mobile_no"),
    }


# ══════════════════════════════════════════════════════════════════
# TOP MOVERS
# ══════════════════════════════════════════════════════════════════

MOVERS_MODES = {
    1: "Gainers",
    2: "Top Change",
    3: "Top % Gainers",
    4: "High Price",
    5: "Losers",
}


def get_top_movers(session: Session, mode: int = 1) -> list[dict]:
    """
    Fetch top movers.

    Valid modes (0 returns empty from the API):
      1 = gainers  2 = top absolute change  3 = top % gainers
      4 = high-price leaders  5 = losers
    Returns list of dicts: symbol, price, change, change_pct, volume, trades
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getTopMovers",
        headers=_headers(session),
        data={"mode": str(mode)},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows[:30]:
        if not isinstance(r, dict):
            continue
        result.append({
            "symbol":     r.get("Scrip") or r.get("scrip") or r.get("SCRIP", ""),
            "price":      _safe_float(r.get("Price") or r.get("price") or r.get("PRICE")),
            "change":     _safe_float(r.get("Change") or r.get("change") or r.get("CHANGE")),
            "change_pct": _safe_float(r.get("PerChange") or r.get("perChange") or r.get("PERCHANGE")),
            "volume":     _safe_int(r.get("Volume") or r.get("volume") or r.get("VOLUME")),
            "trades":     _safe_int(r.get("Trades") or r.get("trades") or r.get("TRADES")),
        })
    return result


# ══════════════════════════════════════════════════════════════════
# EXCHANGE STATE / INDICES
# ══════════════════════════════════════════════════════════════════

_EXCHANGE_STATE_SUMMARY_KEYS = {"pre_vol", "adv", "dec", "unc", "tot_vol", "total"}


def get_exchange_state(session: Session) -> dict:
    """
    Fetch live exchange state.

    Returns:
      {
        "summary": { "adv", "dec", "unc", "total", "pre_volume", "total_volume" },
        "indices": [ { "name", "current_index", "high_index", "low_index",
                       "net_change", "volume_traded", "value_traded" }, ... ]
      }
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getexst",
        headers=_headers(session),
        data={},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return {}
    adata = body.get("aData", {})
    if not isinstance(adata, dict):
        return {}

    summary = {
        "adv":          _safe_int(adata.get("adv")),
        "dec":          _safe_int(adata.get("dec")),
        "unc":          _safe_int(adata.get("unc")),
        "total":        _safe_int(adata.get("total")),
        "pre_volume":   adata.get("pre_vol"),
        "total_volume": adata.get("tot_vol"),
    }

    indices = []
    for key, val in adata.items():
        if key in _EXCHANGE_STATE_SUMMARY_KEYS or not isinstance(val, dict):
            continue
        indices.append({
            "name":          key,
            "current_index": _safe_float(val.get("current_index")),
            "high_index":    _safe_float(val.get("high_index")),
            "low_index":     _safe_float(val.get("low_index")),
            "net_change":    _safe_float(val.get("net_change")),
            "volume_traded": val.get("volume_traded"),
            "value_traded":  val.get("value_traded"),
        })

    return {"summary": summary, "indices": indices}


def get_indices_summary(session: Session) -> list[dict]:
    """
    Fetch indices summary table (one row per market segment).

    Returns list of dicts with keys mapped from aHeader:
      market_code, market_status, total_trades, total_volume, total_value, ...
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getIndicesSummary",
        headers=_headers(session),
        data={},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    rows   = body.get("aData", [])
    header = body.get("aHeader", [])
    if not isinstance(rows, list):
        return []
    if not header:
        return rows
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mapped = {}
        for idx, col_name in enumerate(header, start=1):
            mapped[col_name] = row.get(str(idx))
        parsed.append(mapped)
    return parsed


# ══════════════════════════════════════════════════════════════════
# CONSOLIDATED TRADE LOGS (multi-day net position by symbol)
# ══════════════════════════════════════════════════════════════════

def get_consolidated_trade_logs(
    session: Session,
    symbol:      str = "",
    market_type: str = "",
) -> list[dict]:
    """
    Fetch consolidated (multi-day) trade logs showing net buy/sell per symbol.

    Fields: client_code, symbol, market, buy_qty, buy_rate, buy_amount,
            sell_qty, sell_rate, sell_amount, net_qty, net_rate, net_amount
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getClientTrade",
        headers=_headers(session),
        data={
            "account": session.user_id,
            "mktType": market_type,
            "symbol":  symbol,
            "type":    "S",
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        result.append({
            "client_code":  r.get("CLIENT_CODE"),
            "symbol":       r.get("ITEM_SYMBOL"),
            "market":       r.get("MARKET_TYPE"),
            "buy_qty":      _safe_int(r.get("BQTY")),
            "buy_rate":     _safe_float(r.get("BRATE")),
            "buy_amount":   _safe_float(r.get("BAMT")),
            "sell_qty":     _safe_int(r.get("SQTY")),
            "sell_rate":    _safe_float(r.get("SRATE")),
            "sell_amount":  _safe_float(r.get("SAMT")),
            "net_qty":      _safe_int(r.get("NQTY")),
            "net_rate":     _safe_float(r.get("NRATE")),
            "net_amount":   _safe_float(r.get("NAMT")),
        })
    return result


# ══════════════════════════════════════════════════════════════════
# MARKET STATUS
# ══════════════════════════════════════════════════════════════════

_MARKET_STATUS_LABELS = {
    "O":   "OPEN",
    "C":   "CLOSED",
    "H":   "HALTED",
    "OHO": "CLOSED",   # Over / Halted / Off
    "PRE": "PRE-OPEN",
}


def get_market_status(session: Session) -> dict:
    """
    Fetch current market open/close status.

    Returns:
      { "status": "OPEN"|"CLOSED"|"HALTED"|"PRE-OPEN",
        "raw_status": "<code from API>",
        "exec_time": "...", "date_time": "..." }
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getMktStatus",
        headers=_headers(session),
        data={},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not isinstance(body, dict):
        return {"status": "UNKNOWN"}
    adata = body.get("aData", {})
    raw = adata.get("PSX_STATUS", "") if isinstance(adata, dict) else ""
    label = _MARKET_STATUS_LABELS.get(raw, raw or "UNKNOWN")
    return {
        "status":     label,
        "raw_status": raw,
        "exec_time":  body.get("execTime"),
        "date_time":  body.get("dateTime"),
    }


# ══════════════════════════════════════════════════════════════════
# CIRCUIT-BREAKER LIMITS
# ══════════════════════════════════════════════════════════════════

def get_cap_limits(session: Session) -> dict[str, dict]:
    """
    Fetch upper/lower price circuit-breaker limits for all symbols.

    Returns a dict keyed by "MARKET_SYMBOL" (e.g. "01_OGDC"):
      { "upper": <float>, "lower": <float> }

    Use before placing any order to validate price is within circuit.
    An order outside these bounds will be silently rejected by the exchange.
    """
    resp = requests.get(
        f"{BASE_URL}/api_new/getCapLock",
        headers=_headers(session),
        params={"v": "1"},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not isinstance(body, dict):
        return {}
    adata = body.get("aData", {})
    if not isinstance(adata, dict):
        return {}
    headers_row = adata.get("aHeader", [])
    rows        = adata.get("aData",   [])
    if not isinstance(headers_row, list) or not isinstance(rows, list):
        return {}

    # Build column-name → index map
    col = {str(h).upper(): i for i, h in enumerate(headers_row)}
    sym_i    = col.get("SYMBOL_CODE",  col.get("SYMBOL", -1))
    mkt_i    = col.get("MARKET_CODE",  col.get("MARKET", -1))
    upper_i  = col.get("UVAL", -1)
    lower_i  = col.get("LVAL", -1)

    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, list):
            continue
        try:
            sym    = str(row[sym_i]).strip()   if sym_i   >= 0 else ""
            mkt    = str(row[mkt_i]).strip()   if mkt_i   >= 0 else "01"
            upper  = float(row[upper_i])       if upper_i >= 0 else 0.0
            lower  = float(row[lower_i])       if lower_i >= 0 else 0.0
        except (IndexError, TypeError, ValueError):
            continue
        if sym:
            result[f"{mkt}_{sym}"] = {"upper": upper, "lower": lower}
    return result


# ══════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════

def get_notifications(session: Session) -> list[dict]:
    """Fetch broker push notification history."""
    resp = requests.post(
        f"{BASE_URL}/api_new/getNotifications",
        headers=_headers(session),
        data={"account": session.user_id},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    rows = body.get("aData", [])
    return rows if isinstance(rows, list) else []


# ══════════════════════════════════════════════════════════════════
# AVAILABLE CASH
# ══════════════════════════════════════════════════════════════════

def get_available_cash(session: Session, pin: str = "") -> dict:
    """
    Fetch available cash balance from getclientexposure → aBuyPowerAd.

    Note: /api_new/getavailablecash is the *cash withdrawal* endpoint and is
    time-restricted (only open during market hours).  The actual cash balance
    lives in the exposure endpoint which is always available.

    Returns:
      {
        "available_cash":  <float>,   # pCashAmt  — cash available for trading
        "total_cash":      <float>,   # pTotCash  — total cash (= available when unused)
        "collateral":      <float>,   # pCdcAmt   — CDC / collateral value
        "cash_utilized":   <float>,   # pCashUtil — cash already committed
        "col_utilized":    <float>,   # pColUtil  — collateral already committed
      }
    """
    data = _get_client_exposure_raw(session)
    bp = data.get("aBuyPowerAd") if isinstance(data, dict) else None
    if not isinstance(bp, dict):
        return {"available_cash": None}
    return {
        "available_cash": _safe_float(bp.get("pCashAmt")),
        "total_cash":     _safe_float(bp.get("pTotCash")),
        "collateral":     _safe_float(bp.get("pCdcAmt")),
        "cash_utilized":  _safe_float(bp.get("pCashUtil")),
        "col_utilized":   _safe_float(bp.get("pColUtil")),
    }


# ══════════════════════════════════════════════════════════════════
# WITHDRAWAL REQUESTS
# ══════════════════════════════════════════════════════════════════

_WD_STATUS_LABELS = {
    "P": "Pending",
    "C": "Cancelled",
    "A": "Approved",
    "R": "Rejected",
    "F": "Fetched",
    "N": "Not Reviewed",
}


def get_pending_withdrawals(session: Session, pin: str = "") -> list[dict]:
    """
    Fetch pending withdrawal / pay requests.

    Each item: serial_no, requested_date, amount_requested, approved_amount,
               pay_method, description, status, status_label
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getpendingwithdrawreqs",
        headers=_headers(session),
        data={"account": session.user_id, "pin": pin},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        status = str(r.get("STATUS", ""))
        result.append({
            "serial_no":        r.get("SERIAL_NO"),
            "requested_date":   r.get("REQUESTED_DATE"),
            "amount_requested": _safe_float(r.get("AMOUNT_REQUESTED")),
            "approved_amount":  _safe_float(r.get("APPROVED_AMOUNT")),
            "pay_method":       r.get("PAY_METHOD"),
            "description":      r.get("DESCRIPTION"),
            "status":           status,
            "status_label":     _WD_STATUS_LABELS.get(status, status),
        })
    return result


def submit_withdrawal(session: Session, amount: str, pin: str) -> dict:
    """
    Submit a withdrawal / pay request.

    Returns: { "success": bool, "message": str }
    method is hardcoded to "pickup" (as in the app).
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/payrequest",
        headers=_headers(session),
        data={
            "account": session.user_id,
            "pin":     pin,
            "amount":  amount,
            "method":  "pickup",
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    return {
        "success": bool(body.get("success")),
        "message": body.get("message") or body.get("aData", ""),
    }


def cancel_withdrawal(session: Session, serial_no: str, pin: str) -> dict:
    """
    Cancel a pending withdrawal by SERIAL_NO.

    Returns: { "success": bool, "message": str }
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/cancelPayRequest",
        headers=_headers(session),
        data={
            "sNo":     serial_no,
            "account": session.user_id,
            "pin":     pin,
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    return {
        "success": bool(body.get("success")),
        "message": body.get("message", ""),
    }


# ══════════════════════════════════════════════════════════════════
# ACCOUNT STATEMENT (ledger)
# ══════════════════════════════════════════════════════════════════

_STATEMENT_KEYS = {
    "2": "date",
    "3": "description",
    "5": "debit",
    "6": "credit",
    "7": "balance",
}


def get_account_statement(
    session:      Session,
    from_date:    str,      # dd-MM-yyyy
    to_date:      str,      # dd-MM-yyyy
    ledger_type:  str = "",
    num:          str = "",
) -> list[dict]:
    """
    Fetch account ledger / statement for a date range.

    from_date / to_date format: dd-MM-yyyy  (e.g. "01-05-2026")
    Returns list of: { date, description, debit, credit, balance }
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getaccountstats",
        headers=_headers(session),
        data={
            "account":     session.user_id,
            "fromDate":    from_date,
            "toDate":      to_date,
            "ledgerType":  ledger_type,
            "num":         num,
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        entry = {}
        for key, name in _STATEMENT_KEYS.items():
            val = r.get(key, "")
            if val == "null":
                val = ""
            entry[name] = val
        entry["debit"]   = _safe_float(entry.get("debit"))
        entry["credit"]  = _safe_float(entry.get("credit"))
        entry["balance"] = _safe_float(entry.get("balance"))
        result.append(entry)
    return result


# ══════════════════════════════════════════════════════════════════
# SCRIP / ITEM DETAIL
# ══════════════════════════════════════════════════════════════════

def get_item_detail(session: Session, symbol: str, mkt_type: str = "REG") -> dict:
    """
    Fetch live quote + fundamental details for a scrip.

    Returns:
      {
        "live": { ASK_PRICE, BID_PRICE, LAST_TRADE_PRICE, NET_CHANGE,
                  HIGH_PRICE, LOW_PRICE, OPEN_PRICE, TOTAL_TRADED_VOLUME, ... },
        "fundamentals": { haircut, acceptable_qty, face_value, var_margin,
                          sector, settlement_type, effective, lot_size }
      }
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getitemdetail",
        headers=_headers(session),
        data={"symbol": symbol, "mktType": mkt_type},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if body.get("msg") and not body.get("aData"):
        return {"error": body.get("msg")}
    adata = body.get("aData", {})
    if not isinstance(adata, dict):
        return {}

    live = adata.get("mf", {}) or {}

    # Fundamentals use numeric keys "1"-"8"
    id_obj = adata.get("id", {}) or {}
    _fund_keys = {
        "1": "haircut",
        "2": "acceptable_qty",
        "3": "face_value",
        "4": "var_margin",
        "5": "sector",
        "6": "settlement_type",
        "7": "effective",
        "8": "lot_size",
    }
    fundamentals = {name: id_obj.get(k) for k, name in _fund_keys.items()}

    return {
        "symbol":       symbol,
        "market":       mkt_type,
        "live":         live,
        "fundamentals": fundamentals,
    }


# ══════════════════════════════════════════════════════════════════
# ITEM PERIODIC DATA (OHLC stats per duration)
# ══════════════════════════════════════════════════════════════════

_PERIODIC_KEYS = {
    "1": "low_price",
    "2": "high_price",
    "3": "avg_price",
    "4": "low_volume",
    "5": "high_volume",
    "6": "avg_volume",
}

PERIODIC_DURATIONS = ["5DY", "1MO", "6MO", "YTD", "1YR", "5YR"]


def get_item_periodic_data(
    session:  Session,
    symbol:   str,
    duration: str = "1MO",
) -> list[dict]:
    """
    Fetch periodic OHLC / volume stats for a symbol.

    duration: one of 5DY, 1MO, 6MO, YTD, 1YR, 5YR
    Returns list of { low_price, high_price, avg_price,
                       low_volume, high_volume, avg_volume }
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getitemperiodicdata",
        headers=_headers(session),
        data={"symbol": symbol, "duration": duration},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if body.get("msg"):
        return []
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        entry = {name: _safe_float(r.get(k)) for k, name in _PERIODIC_KEYS.items()}
        result.append(entry)
    return result


# ══════════════════════════════════════════════════════════════════
# SYMBOL INDEX CHART
# ══════════════════════════════════════════════════════════════════

def get_symbol_chart(
    session:  Session,
    symbol:   str,
    mkt_type: str = "REG",
    index:    str = "",
    mode:     str = "1",
) -> list[dict]:
    """
    Fetch price/volume chart series for a symbol (up to 100 points).

    Returns list of { time, price, volume }
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getSymbolIndexChart",
        headers=_headers(session),
        data={
            "mktType": mkt_type,
            "symbol":  symbol,
            "index":   index,
            "mode":    mode,
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows[:100]:
        if not isinstance(r, dict):
            continue
        raw_time = r.get("LAST_TRADE_TIME", "")
        # app splits on space and uses index [1] as the clock portion
        time_parts = str(raw_time).split()
        clock = time_parts[1] if len(time_parts) > 1 else raw_time
        result.append({
            "time":   clock,
            "price":  _safe_float(r.get("LAST_TRADE_PRICE")),
            "volume": _safe_int(r.get("LAST_TRADE_VOLUME")),
        })
    return result


# ══════════════════════════════════════════════════════════════════
# FEED BY WATCH TYPE
# ══════════════════════════════════════════════════════════════════

def get_feed_by_watch_type(
    session: Session,
    feed_type: str,  # "S"=sector "I"=index "F"=futures "U"=upper-cap "L"=lower-cap
    code:    str = "",
) -> list[dict]:
    """
    Fetch live symbol feed filtered by watch type.

    feed_type:
      "S" = by sector (code = sector code)
      "I" = by index  (code = index position as string)
      "F" = futures   (code not required)
      "U" = upper-cap (code = "")
      "L" = lower-cap (code = "")

    Returns list of { symbol, last_price, net_change, volume,
                      bid_price, bid_volume, ask_price, ask_volume }
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getfeedbywatchtype",
        headers=_headers(session),
        data={"type": feed_type, "code": code},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        result.append({
            "symbol":     r.get("SYMBOL_CODE", ""),
            "last_price": _safe_float(r.get("LAST_TRADE_PRICE")),
            "net_change": _safe_float(r.get("NET_CHANGE")),
            "volume":     _safe_int(r.get("TOTAL_TRADED_VOLUME")),
            "bid_price":  _safe_float(r.get("BID_PRICE")),
            "bid_volume": _safe_int(r.get("BID_VOLUME")),
            "ask_price":  _safe_float(r.get("ASK_PRICE")),
            "ask_volume": _safe_int(r.get("ASK_VOLUME")),
        })
    return result


# ══════════════════════════════════════════════════════════════════
# TRADE ITEM DETAIL (open position drill-down)
# ══════════════════════════════════════════════════════════════════

def get_trade_item_detail(
    session:  Session,
    symbol:   str,
    pos_type: str = "OPEN",   # "OPEN" | "CDC"
) -> list[dict]:
    """
    Fetch open position / CDC trade detail for a specific symbol.

    pos_type="OPEN": fields market, initial_date, open_rate, quantity,
                     markup_amount, investment_amt, mtm_amount, avg_rate
    pos_type="CDC":  fields trade_date, cdc_qty, cdc_rate, cdc_amt
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getTradeItemRs",
        headers=_headers(session),
        data={
            "account": session.user_id,
            "symbol":  symbol,
            "posType": pos_type,
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if pos_type == "CDC":
            result.append({
                "trade_date": r.get("TRADE_DATE"),
                "cdc_qty":    _safe_int(r.get("CDC_QTY")),
                "cdc_rate":   _safe_float(r.get("CDC_RATE")),
                "cdc_amount": _safe_float(r.get("CDC_AMT")),
            })
        else:
            result.append({
                "market":         r.get("MARKET"),
                "initial_date":   r.get("INITIAL_DATE"),
                "open_rate":      _safe_float(r.get("OPEN_RATE")),
                "quantity":       _safe_int(r.get("QUANTITY")),
                "premium_pct":    _safe_float(r.get("PREMIUM_PER")),
                "premium_rate":   _safe_float(r.get("PREMIUM_RATE")),
                "days":           r.get("DAYS"),
                "markup_amount":  _safe_float(r.get("MARKUP_AMOUNT")),
                "investment_amt": _safe_float(r.get("INVESTMENT_AMT")),
                "mtm_amount":     _safe_float(r.get("MTM_AMOUNT")),
                "release_amount": _safe_float(r.get("RELEASE_AMOUNT")),
                "avg_rate":       _safe_float(r.get("AVG_RATE")),
            })
    return result


# ══════════════════════════════════════════════════════════════════
# CLIENT EXPOSURE DETAILS (executed vs pending per symbol)
# ══════════════════════════════════════════════════════════════════

def get_client_exposure_details(
    session:  Session,
    symbol:   str,
    mkt_type: str = "REG",  # "REG" | "FUT"
    mode:     str = "E",    # "E"=executed  "O"=pending/open
) -> list[dict]:
    """
    Fetch executed or pending order detail for a specific symbol.

    Returns list of { side, quantity, price }
    where side is the raw value from key "2" (e.g. "BUY", "SELL", etc.)
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getClientExposureDetails",
        headers=_headers(session),
        data={
            "mode":    mode,
            "mktType": "FUT" if mkt_type.upper() == "FUT" else "REG",
            "account": session.user_id,
            "symbol":  symbol,
        },
        timeout=30,
        verify=False,
    )
    body = resp.json()
    rows = body.get("aData", [])
    if not isinstance(rows, list):
        return []
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        result.append({
            "side":     r.get("2", ""),
            "quantity": r.get("3", ""),
            "price":    r.get("4", ""),
        })
    return result


# ══════════════════════════════════════════════════════════════════
# EXPOSURE SUMMARY BY CATEGORY
# ══════════════════════════════════════════════════════════════════

def get_exposure_summary_cat(session: Session) -> dict:
    """
    Fetch exposure summary by category (includes MTM P/L, loan, markup etc.).

    Returns flat dict with keys: ledger_balance, cdc_amount, inventory_sold,
    held_amount, realized_pl, mtm_pl, expense_amount, net_worth, loan_amount,
    markup_amount, cdc_amount_hc, account_pct, account_pct_lev, extra_1, fpr_amount
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getExposureSmryCat",
        headers=_headers(session),
        data={"account": session.user_id},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return {}
    r = body.get("aData", {})
    if not isinstance(r, dict):
        return {}

    def _nn(v):
        return _safe_float(None if str(v or "").strip() == "null" else v)

    return {
        "ledger_balance":   _nn(r.get("p_led_bal")),
        "cdc_amount":       _nn(r.get("p_cdc_amt")),
        "inventory_sold":   _nn(r.get("p_inventory_sold")),
        "held_amount":      _nn(r.get("p_held_amt")),
        "realized_pl":      _nn(r.get("p_realized_pl_amt")),
        "mtm_pl":           _nn(r.get("p_mtm_PL")),
        "expense_amount":   _nn(r.get("p_expense_amt")),
        "net_worth":        _nn(r.get("p_net_worth")),
        "loan_amount":      _nn(r.get("p_loan_amt")),
        "markup_amount":    _nn(r.get("p_markup_amt")),
        "cdc_amount_hc":    _nn(r.get("p_cdc_amt_hc")),
        "account_pct":      _nn(r.get("p_acc_per")),
        "account_pct_lev":  _nn(r.get("p_acc_per_lev")),
        "extra_1":          _nn(r.get("p_extra_1")),
        "fpr_amount":       _nn(r.get("p_fpr_amt")),
    }


# ══════════════════════════════════════════════════════════════════
# CLIENT EXPOSURE BY CATEGORY (collaterals / open positions)
# ══════════════════════════════════════════════════════════════════

_EXP_CAT_COLLATERAL_COLS = [
    "VX_Symbol", "QT_Quantity", "AX_Avg. Buy Rate", "QT_Sold Quantity",
    "AX_Avg. Sell Rate", "AT_MTM", "AT_MTM Amount", "QT_Pending Sell",
    "AT_Settled P/L", "AT_Unsettled P/L",
]

_EXP_CAT_OPEN_COLS = [
    "VX_Symbol", "VX_Market", "AT_MTM Amount", "QT Total Sell",
    "AX_Avg. Sell Rate", "QT_Pending Buy", "QT_Pending Sell",
    "AT_Settled P/L", "AT_Unsettled P/L", "QT_Net Qty", "QT_MFS Qty",
    "QT_MTS Qty", "QT Total Buy", "AX_Avg. Buy Rate", "AT_Trans. Amount",
    "AX_MTM Price", "AX_BO Avg. Rate",
]

_EXP_CAT_COL_RENAME = {
    "VX_Symbol":          "symbol",
    "VX_Market":          "market",
    "QT_Quantity":        "quantity",
    "AX_Avg. Buy Rate":   "avg_buy_rate",
    "QT_Sold Quantity":   "sold_qty",
    "AX_Avg. Sell Rate":  "avg_sell_rate",
    "AT_MTM":             "mtm",
    "AT_MTM Amount":      "mtm_amount",
    "QT_Pending Sell":    "pending_sell",
    "QT_Pending Buy":     "pending_buy",
    "AT_Settled P/L":     "settled_pl",
    "AT_Unsettled P/L":   "unsettled_pl",
    "QT Total Sell":      "total_sell",
    "QT Total Buy":       "total_buy",
    "QT_Net Qty":         "net_qty",
    "QT_MFS Qty":         "mfs_qty",
    "QT_MTS Qty":         "mts_qty",
    "AT_Trans. Amount":   "trans_amount",
    "AX_MTM Price":       "mtm_price",
    "AX_BO Avg. Rate":    "bo_avg_rate",
}


def get_client_exposure_cat(
    session: Session,
    mode:    str = "O",   # "O"=open positions  "I"=collaterals/inventory
) -> list[dict]:
    """
    Fetch client exposure by category (open positions or collaterals).

    mode="O": open positions
    mode="I": collaterals / inventory

    Response uses positional aData arrays mapped via aHeader.
    Returns list of row dicts with human-readable field names.
    """
    resp = requests.post(
        f"{BASE_URL}/api_new/getClientExposureCat",
        headers=_headers(session),
        data={"account": session.user_id, "mode": mode},
        timeout=30,
        verify=False,
    )
    body = resp.json()
    if not body.get("success"):
        return []

    header_raw = body.get("aHeader", [])
    rows_raw   = body.get("aData", [])
    if not isinstance(rows_raw, list):
        return []

    # Build column-index map from aHeader
    if isinstance(header_raw, list):
        header = [str(h) for h in header_raw]
    else:
        header = _EXP_CAT_OPEN_COLS if mode == "O" else _EXP_CAT_COLLATERAL_COLS

    result = []
    for row in rows_raw:
        if isinstance(row, list):
            cells = row
        elif isinstance(row, dict):
            cells = [row.get(str(i), "") for i in range(len(header))]
        else:
            continue
        entry = {}
        for i, col in enumerate(header):
            val = cells[i] if i < len(cells) else ""
            key = _EXP_CAT_COL_RENAME.get(col, col.lower().replace(" ", "_").replace("/", "_"))
            entry[key] = val
        result.append(entry)
    return result


# ══════════════════════════════════════════════════════════════════
# STOP-LOSS ORDER (pushSLO via MF socket)
# ══════════════════════════════════════════════════════════════════

def place_slo_order(
    socket_set,
    session,
    symbol:      str,
    side:        str,        # "1"=buy "2"=sell
    h_order_side: str,       # "BUY" | "SELL" etc.
    volume:      str,
    trigger_price: str,      # tag "44" = trigger/limit price for SLO
    stop_price:  str,        # tag "99" = stop price
    order_type:  str = "3",  # "3" = stop-limit
    market_type: str = "REG",
    time_in_force: str = "0",
    pin:         str = "",
) -> str:
    """
    Place a stop-loss order via the MF socket (pushSLO command).

    FIX tag mapping (from decompiled OrderTicketFrags.java):
      "1"   = account
      "38"  = volume
      "40"  = order type
      "44"  = trigger price (commas stripped)
      "54"  = numeric side
      "55"  = symbol
      "59"  = time in force
      "99"  = stop price
      "143" = market type
      "hOrderSide" = human-readable side label
    """
    md5 = _md5_key()
    payload = json.dumps(
        {
            "1":          session.user_id,
            "38":         volume,
            "40":         order_type,
            "44":         trigger_price.replace(",", ""),
            "54":         side,
            "55":         symbol,
            "59":         time_in_force,
            "65":         "",
            "99":         stop_price.replace(",", ""),
            "111":        "",
            "126":        "",
            "143":        market_type,
            "167":        "",
            "200":        "",
            "201":        "",
            "202":        "",
            "206":        "",
            "7200":       "",
            "hOrderSide": h_order_side,
            "pin":        pin,
            "remarks":    "order",
            "ordHash":    md5,
            "utc":        _utc(),
        },
        separators=(",", ":"),
    )
    socket_set.mf.send('[9,{"key":"pushSLO","val":' + payload + "}]")
    log.info(f"[ORDER] SLO  {symbol}  side={side}  qty={volume}  trigger={trigger_price}  stop={stop_price}  ordHash={md5}")
    return md5
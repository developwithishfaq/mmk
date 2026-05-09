"""
account.py — Account, position, exposure, and withdrawal routes.

Endpoints
---------
GET  /account/positions
GET  /account/summary
GET  /account/raw
GET  /account/cash
GET  /account/withdrawals
POST /account/withdrawal
POST /account/withdrawal/cancel
GET  /account/statement
GET  /account/trade-detail/{symbol}
GET  /account/exposure-details/{symbol}
GET  /account/exposure-summary-cat
GET  /account/exposure-cat
GET  /notifications
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from mmk_api import (
    cancel_withdrawal,
    get_account_statement,
    get_account_summary,
    get_available_cash,
    get_client_exposure_cat,
    get_client_exposure_details,
    get_exposure_summary_cat,
    get_notifications,
    get_open_positions,
    get_pending_withdrawals,
    get_trade_item_detail,
    submit_withdrawal,
)
from mmk_backend.api.deps import SessionDep
from mmk_backend.runtime_state import runtime
from mmk_backend.schemas import CancelWithdrawalRequest, WithdrawalRequest

router = APIRouter()


@router.get("/account/positions")
def list_positions(session: SessionDep):
    """
    Live REST query — current open positions.
    Fields: symbol, market, net_position, market_rate, open_avg_price,
    open_volume, inventory_holdings, unrealized_mtm, realized_gain_loss, total_mtm.
    """
    positions = get_open_positions(session)
    return {"count": len(positions), "positions": positions}


@router.get("/account/summary")
def account_summary(session: SessionDep):
    """
    Live REST query — account financial summary.
    Keys: ledger_balance, net_worth, realized_pl, unrealized_mtm_profit,
    unrealized_mtm_loss, pending_buy, pending_sell, cash_margin_req, ...
    """
    return get_account_summary(session)


@router.get("/account/raw")
def account_raw(session: SessionDep):
    """Debug: raw aData from getclientexposure to inspect actual field names."""
    from mmk_api import _get_client_exposure_raw  # internal helper
    return _get_client_exposure_raw(session)


@router.get("/account/cash")
def route_available_cash(session: SessionDep):
    """Available cash balance for position sizing."""
    return get_available_cash(session, pin=runtime.pin)


@router.get("/account/withdrawals")
def route_pending_withdrawals(session: SessionDep):
    """Pending withdrawal / pay requests."""
    return get_pending_withdrawals(session, pin=runtime.pin)


@router.post("/account/withdrawal")
def route_submit_withdrawal(req_body: WithdrawalRequest, session: SessionDep):
    """Submit a withdrawal / pay request."""
    effective_pin = req_body.pin.strip() or runtime.pin
    return submit_withdrawal(session, amount=req_body.amount, pin=effective_pin)


@router.post("/account/withdrawal/cancel")
def route_cancel_withdrawal(req_body: CancelWithdrawalRequest, session: SessionDep):
    """Cancel a pending withdrawal by serial number."""
    effective_pin = req_body.pin.strip() or runtime.pin
    return cancel_withdrawal(session, serial_no=req_body.serial_no, pin=effective_pin)


@router.get("/account/statement")
def route_account_statement(
    session: SessionDep,
    from_date:   str = "",
    to_date:     str = "",
    ledger_type: str = "",
    num:         str = "",
):
    """Account ledger / statement for a date range. Format: dd-MM-yyyy."""
    if not from_date or not to_date:
        today     = datetime.now()
        from_date = from_date or (today - timedelta(days=30)).strftime("%d-%m-%Y")
        to_date   = to_date   or today.strftime("%d-%m-%Y")
    return get_account_statement(
        session,
        from_date=from_date,
        to_date=to_date,
        ledger_type=ledger_type,
        num=num,
    )


@router.get("/account/trade-detail/{symbol}")
def route_trade_item_detail(symbol: str, session: SessionDep, pos_type: str = "OPEN"):
    """Detailed position trade rows for one symbol. pos_type: OPEN or CDC."""
    if pos_type not in ("OPEN", "CDC"):
        raise HTTPException(400, "pos_type must be OPEN or CDC")
    return {
        "symbol":   symbol.upper(),
        "pos_type": pos_type,
        "rows":     get_trade_item_detail(session, symbol=symbol.upper(), pos_type=pos_type),
    }


@router.get("/account/exposure-details/{symbol}")
def route_exposure_details(
    symbol: str,
    session: SessionDep,
    mkt_type: str = "REG",
    mode: str = "E",
):
    """Executed or pending exposure details for a symbol. mode: E=executed O=pending."""
    if mode not in ("E", "O"):
        raise HTTPException(400, "mode must be E (executed) or O (pending)")
    return {
        "symbol":   symbol.upper(),
        "mkt_type": mkt_type,
        "mode":     mode,
        "rows":     get_client_exposure_details(
            session, symbol=symbol.upper(), mkt_type=mkt_type, mode=mode,
        ),
    }


@router.get("/account/exposure-summary-cat")
def route_exposure_summary_cat(session: SessionDep):
    """Exposure summary by category (MTM P/L, loan, markup, etc.)."""
    return get_exposure_summary_cat(session)


@router.get("/account/exposure-cat")
def route_exposure_cat(session: SessionDep, mode: str = "O"):
    """Category-level exposure/collateral rows. mode: O=open  I=collaterals."""
    if mode not in ("O", "I"):
        raise HTTPException(400, "mode must be O (open) or I (collaterals)")
    return {"mode": mode, "rows": get_client_exposure_cat(session, mode=mode)}


@router.get("/notifications")
def notifications(session: SessionDep):
    """Broker push notification history."""
    items = get_notifications(session)
    return {"count": len(items), "notifications": items}

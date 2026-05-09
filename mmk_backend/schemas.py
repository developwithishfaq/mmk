"""Pydantic request/response shapes for the REST API."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from mmk_api import MARKET_REG, PERIODIC_DURATIONS
from mmk_backend.runtime_state import runtime


class PlaceOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"] = "limit"
    price: str = "0"
    volume: str
    pin: str = Field(default_factory=lambda: runtime.pin)
    market_type: str = MARKET_REG


class CancelOrderRequest(BaseModel):
    symbol: str
    exch_order_id: str
    house_order_id: str
    order_side: str
    market_type: str = MARKET_REG
    pin: str = Field(default_factory=lambda: runtime.pin)


class CancelByExchIdRequest(BaseModel):
    exch_order_id: str
    pin: str = Field(default_factory=lambda: runtime.pin)


class LoginRequest(BaseModel):
    user_id: str
    password: str
    pin: str = Field(default_factory=lambda: runtime.pin)


class ChangeOrderRequest(BaseModel):
    exch_order_id: str
    new_price: str
    new_volume: str
    pin: str = ""


class MboMbpRequest(BaseModel):
    symbol: str
    market: str = MARKET_REG


class AutoTradeCreate(BaseModel):
    symbol: str
    account_balance: float = 1_000.0
    risk_per_trade: float = 0.02
    enabled: bool = False
    dry_run: bool = False
    order_type: Literal["market", "limit"] = "market"
    min_tick_interval_sec: float = 0.0
    exit_cooldown_sec: float = 10.0
    warmup_ticks: int = 10
    max_pyramids: int = 2
    pin: str = ""


class AutoTradePatch(BaseModel):
    enabled: Optional[bool] = None
    dry_run: Optional[bool] = None
    account_balance: Optional[float] = None
    risk_per_trade: Optional[float] = None
    order_type: Optional[Literal["market", "limit"]] = None
    min_tick_interval_sec: Optional[float] = None
    exit_cooldown_sec: Optional[float] = None
    warmup_ticks: Optional[int] = None
    max_pyramids: Optional[int] = None
    pin: Optional[str] = None


class AutoTradeTestTick(BaseModel):
    last: float
    low: float
    high: float
    prev_close: float
    place: bool = False
    market_open: bool = True


class WithdrawalRequest(BaseModel):
    amount: str
    pin: str = ""


class CancelWithdrawalRequest(BaseModel):
    serial_no: str
    pin: str = ""


class SloOrderRequest(BaseModel):
    symbol: str
    side: str
    h_order_side: str
    volume: str
    trigger_price: str
    stop_price: str
    order_type: str = "3"
    market_type: str = "REG"
    time_in_force: str = "0"
    pin: str = ""


class DailyTraderConfigPatch(BaseModel):
    enabled: Optional[bool] = None
    feed_type: Optional[str] = None
    feed_code: Optional[str] = None
    extra_watchlist: Optional[list[str]] = None
    min_change_pct: Optional[float] = None
    min_volume: Optional[int] = None
    max_spread_pct: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    max_concurrent: Optional[int] = None
    max_orders_per_day: Optional[int] = None
    max_buy_amount_per_trade: Optional[float] = None
    max_sell_amount_per_trade: Optional[float] = None
    daily_max_buy_amount: Optional[float] = None
    daily_max_sell_amount: Optional[float] = None
    max_change_pct: Optional[float] = None
    use_vwap_filter: Optional[bool] = None
    vwap_max_above_pct: Optional[float] = None
    tick_driven: Optional[bool] = None
    target_pct: Optional[float] = None
    stop_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    use_slo: Optional[bool] = None
    daily_loss_limit_pct: Optional[float] = None
    cooldown_after_loss_s: Optional[int] = None
    entry_start_hhmm: Optional[str] = None
    entry_stop_hhmm: Optional[str] = None
    force_exit_hhmm: Optional[str] = None
    scan_interval_sec: Optional[int] = None
    monitor_interval_sec: Optional[int] = None
    entry_offset_pct: Optional[float] = None
    exit_offset_pct: Optional[float] = None


class DailyTraderHaltRequest(BaseModel):
    reason: str = "manual"


class DailyTraderCloseRequest(BaseModel):
    pos_id: str
    reason: str = "manual"


__all__ = [
    "PERIODIC_DURATIONS",
    "PlaceOrderRequest",
    "CancelOrderRequest",
    "CancelByExchIdRequest",
    "LoginRequest",
    "ChangeOrderRequest",
    "MboMbpRequest",
    "AutoTradeCreate",
    "AutoTradePatch",
    "AutoTradeTestTick",
    "WithdrawalRequest",
    "CancelWithdrawalRequest",
    "SloOrderRequest",
    "DailyTraderConfigPatch",
    "DailyTraderHaltRequest",
    "DailyTraderCloseRequest",
]

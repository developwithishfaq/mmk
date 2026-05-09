"""
PSXPositionManager — position sizing, pyramid, and stop logic for auto-trading.
"""


class PSXPositionManager:
    def __init__(self, symbol, account_balance, risk_per_trade=0.02):
        self.symbol = symbol
        self.balance = account_balance
        self.risk_pct = risk_per_trade  # 2% risk per trade

        self.state = "FLAT"          # FLAT | LONG
        self.entries = []            # list of {price, qty, time}
        self.avg_entry_price = 0
        self.total_qty = 0
        self.pyramid_count = 0
        self.max_pyramids = 2        # max 2 add-ons after initial entry

        self.stop_loss_price = None
        self.initial_entry_price = None

    def get_position_size(self, entry_price, stop_price):
        """Kelly-based position sizing"""
        risk_amount = self.balance * self.risk_pct
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share == 0:
            return 0
        qty = int(risk_amount / risk_per_share)
        return qty

    def _spent_cash(self) -> float:
        """Total cash already deployed in current open position."""
        return sum(float(e["price"]) * float(e["qty"]) for e in self.entries)

    def _cap_by_available_cash(self, desired_qty: int, price: float) -> int:
        """
        Hard cap: total spend across all entries must not exceed `self.balance`.
        Returns the largest qty that fits the remaining cash at `price`.
        """
        if price <= 0 or desired_qty <= 0:
            return 0
        remaining = max(0.0, float(self.balance) - self._spent_cash())
        max_by_cash = int(remaining // price)
        return max(0, min(int(desired_qty), max_by_cash))

    def on_signal(self, signal, tick):
        last = float(tick["last"])
        low = float(tick["low"])
        high = float(tick["high"])
        atr = (high - low)  # simple proxy; use proper ATR in production

        action = None

        if self.state == "FLAT":
            if signal == "BUY":
                stop = last - (1.5 * atr)
                desired_qty = self.get_position_size(last, stop)
                qty = self._cap_by_available_cash(desired_qty, last)

                if qty > 0:
                    self.state = "LONG"
                    self.initial_entry_price = last
                    self.stop_loss_price = stop
                    self.entries.append({"price": last, "qty": qty})
                    self.total_qty = qty
                    self.avg_entry_price = last
                    self.pyramid_count = 0
                    action = {
                        "action": "BUY",
                        "qty": qty,
                        "price": last,
                        "stop": round(stop, 2),
                        "reason": "Initial Entry",
                        "desired_qty": desired_qty,
                        "capped_by_cash": qty < desired_qty,
                    }
                else:
                    action = {
                        "action": "IGNORE",
                        "reason": (
                            "Insufficient balance for 1 share"
                            if desired_qty > 0
                            else "Risk-sized qty is zero (stop too tight or balance/risk too low)"
                        ),
                        "desired_qty": desired_qty,
                        "price": last,
                        "balance": self.balance,
                    }

            elif signal == "SELL":
                action = {"action": "IGNORE", "reason": "Can't short on PSX, currently FLAT"}

        elif self.state == "LONG":
            if last <= self.stop_loss_price:
                action = self._exit_position(last, "STOP LOSS HIT")

            elif signal == "SELL":
                action = self._exit_position(last, "SELL Signal")

            elif signal == "BUY":
                profit_pts = last - self.avg_entry_price

                if self.pyramid_count >= self.max_pyramids:
                    action = {"action": "IGNORE", "reason": "Max pyramids reached"}

                elif profit_pts <= 0:
                    action = {"action": "IGNORE", "reason": "No pyramid on losing position"}

                else:
                    desired_add = int(self.total_qty * 0.5)
                    add_qty = self._cap_by_available_cash(desired_add, last)
                    new_stop = self.avg_entry_price

                    if add_qty > 0:
                        self.entries.append({"price": last, "qty": add_qty})
                        self.total_qty += add_qty
                        self.avg_entry_price = self._calc_avg()
                        self.stop_loss_price = new_stop
                        self.pyramid_count += 1

                        action = {
                            "action": "PYRAMID BUY",
                            "add_qty": add_qty,
                            "price": last,
                            "new_avg": round(self.avg_entry_price, 2),
                            "new_stop": round(new_stop, 2),
                            "pyramid_level": self.pyramid_count,
                            "desired_qty": desired_add,
                            "capped_by_cash": add_qty < desired_add,
                        }
                    else:
                        action = {
                            "action": "IGNORE",
                            "reason": "Pyramid skipped: balance cap reached",
                            "desired_qty": desired_add,
                            "price": last,
                            "balance": self.balance,
                            "spent": round(self._spent_cash(), 2),
                        }

        return action

    def _calc_avg(self):
        total_cost = sum(e["price"] * e["qty"] for e in self.entries)
        total_qty = sum(e["qty"] for e in self.entries)
        return total_cost / total_qty if total_qty else 0

    def _exit_position(self, price, reason):
        pnl = (price - self.avg_entry_price) * self.total_qty
        result = {
            "action": "SELL (EXIT)",
            "qty": self.total_qty,
            "price": price,
            "avg_entry": round(self.avg_entry_price, 2),
            "pnl": round(pnl, 2),
            "reason": reason,
        }
        self.state = "FLAT"
        self.entries = []
        self.total_qty = 0
        self.avg_entry_price = 0
        self.stop_loss_price = None
        self.pyramid_count = 0
        self.initial_entry_price = None
        return result

    def snapshot_runtime_state(self) -> dict:
        return {
            "state": self.state,
            "entries": list(self.entries),
            "avg_entry_price": self.avg_entry_price,
            "total_qty": self.total_qty,
            "pyramid_count": self.pyramid_count,
            "stop_loss_price": self.stop_loss_price,
            "initial_entry_price": self.initial_entry_price,
        }

    def restore_runtime_state(self, data: dict) -> None:
        if not data:
            return
        self.state = data.get("state", "FLAT")
        self.entries = list(data.get("entries", []))
        self.avg_entry_price = float(data.get("avg_entry_price", 0) or 0)
        self.total_qty = int(data.get("total_qty", 0) or 0)
        self.pyramid_count = int(data.get("pyramid_count", 0) or 0)
        sl = data.get("stop_loss_price")
        self.stop_loss_price = float(sl) if sl is not None else None
        ie = data.get("initial_entry_price")
        self.initial_entry_price = float(ie) if ie is not None else None


def manager_from_strategy_row(row: dict) -> PSXPositionManager:
    sym = str(row.get("symbol", "")).strip().upper()
    bal = float(row.get("account_balance", 0) or 0)
    risk = float(row.get("risk_per_trade", 0.02) or 0.02)
    m = PSXPositionManager(sym, bal, risk)
    mp = row.get("max_pyramids")
    if mp is not None:
        m.max_pyramids = int(mp)
    m.restore_runtime_state(row.get("runtime") or {})
    return m

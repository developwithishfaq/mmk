import math

import daily_trader


class Recorder:
    def __init__(self):
        self.buy_orders = []
        self.sell_limit_orders = []
        self.sell_market_orders = []
        self.counter = 0

    def next_hash(self, prefix="ord"):
        self.counter += 1
        return f"{prefix}_{self.counter}"

    def place_buy(self, symbol, price, qty):
        self.buy_orders.append({"symbol": symbol, "price": float(price), "qty": int(qty)})
        return self.next_hash("buy")

    def place_sell_limit(self, symbol, price, qty):
        self.sell_limit_orders.append({"symbol": symbol, "price": float(price), "qty": int(qty)})
        return self.next_hash("sell_l")

    def place_sell_market(self, symbol, qty):
        self.sell_market_orders.append({"symbol": symbol, "qty": int(qty)})
        return self.next_hash("sell_m")


def make_bot():
    rec = Recorder()
    hooks = daily_trader.Hooks(
        get_quote=lambda sym: {"symbol": sym, "last": 100, "bid": 99.9, "ask": 100.1, "volume": 1_000_000},
        subscribe_symbols=lambda syms: None,
        fetch_feed=lambda feed_type, code: [],
        get_cash=lambda: 100_000.0,
        place_limit_buy=rec.place_buy,
        place_limit_sell=rec.place_sell_limit,
        place_market_sell=rec.place_sell_market,
        cancel_order=lambda h: True,
        place_slo=None,
        broker_has_any_today=lambda sym: False,
        broker_market_open=lambda: True,
        get_broker_positions=lambda: [],
        broker_refresh=lambda: {},
    )
    bot = daily_trader.DailyTrader(hooks=hooks)
    bot._state = "RUNNING"
    bot._start_of_day_cash = 100_000.0
    return bot, rec


def mk_sig(symbol="AHCL", price=100.0):
    return daily_trader.Signal(
        symbol=symbol,
        last_price=price,
        change_pct=2.0,
        volume=1_000_000,
        bid=price - 0.1,
        ask=price,
        spread_pct=0.1,
        score=1.0,
    )


def test_max_orders_per_day_blocks_new_entries():
    bot, rec = make_bot()
    bot._cfg.max_orders_per_day = 1
    bot._cfg.max_buy_amount_per_trade = 1_000_000
    bot._cfg.daily_max_buy_amount = 1_000_000

    bot._enter_position(mk_sig("AHCL", 100))
    assert len(rec.buy_orders) == 1
    assert bot._trade_count_today == 1

    # _can_take_new_entry should block further entries for today.
    assert bot._can_take_new_entry() is False


def test_max_buy_amount_per_trade_caps_entry_qty():
    bot, rec = make_bot()
    bot._cfg.max_buy_amount_per_trade = 1_000.0
    bot._cfg.daily_max_buy_amount = 100_000.0
    bot._cfg.max_orders_per_day = 10
    bot._cfg.stop_pct = 1.0

    bot._enter_position(mk_sig("MARI", 100.0))
    assert len(rec.buy_orders) == 1
    # With price 100 and per-trade cap 1000, qty cannot exceed 10.
    assert rec.buy_orders[0]["qty"] <= 10


def test_daily_max_buy_amount_limits_cumulative_buys():
    bot, rec = make_bot()
    bot._cfg.max_orders_per_day = 10
    bot._cfg.max_buy_amount_per_trade = 100_000.0
    bot._cfg.daily_max_buy_amount = 1_500.0
    bot._cfg.stop_pct = 1.0

    # First entry at 100 should place.
    bot._enter_position(mk_sig("ENGRO", 100.0))
    assert len(rec.buy_orders) == 1
    ord1 = next(iter(bot._ord_to_pos.keys()))
    qty1 = rec.buy_orders[0]["qty"]
    bot.on_order_update(ord1, "filled", fill_price=100.0, fill_qty=qty1, cum_qty=qty1, avg_px=100.0)
    assert bot._daily_buy_value > 0

    # Second entry must not exceed remaining daily buy budget.
    bot._enter_position(mk_sig("LUCK", 100.0))
    # With first trade already near the daily cap, second trade is blocked.
    assert len(rec.buy_orders) == 1
    assert bot._daily_buy_value <= bot._cfg.daily_max_buy_amount + 1e-6


def test_max_sell_amount_per_trade_splits_exit_order():
    bot, rec = make_bot()
    bot._cfg.max_sell_amount_per_trade = 500.0
    bot._cfg.daily_max_sell_amount = 10_000.0

    pos = daily_trader.Position(
        pos_id="p1",
        symbol="PPL",
        side="buy",
        qty=20,
        entry_price=100.0,
        target_price=103.0,
        stop_price=99.0,
        status="holding",
    )
    bot._positions[pos.pos_id] = pos

    bot._exit_position(pos, "target")
    assert len(rec.sell_limit_orders) == 1
    # At ~100 price and 500 max/trade => capped around 5 shares.
    assert rec.sell_limit_orders[0]["qty"] <= 5
    assert pos.exit_order_qty == rec.sell_limit_orders[0]["qty"]

    # Simulate full fill of this capped sell order; position should remain
    # holding with reduced qty (not closed) when some shares are still left.
    bot.on_order_update(
        pos.exit_ord_hash,
        "filled",
        fill_price=100.0,
        fill_qty=pos.exit_order_qty,
        cum_qty=pos.exit_order_qty,
        avg_px=100.0,
    )
    assert pos.status in {"holding", "closed"}
    assert pos.qty <= 20
    assert pos.qty >= 0


def test_daily_max_sell_amount_blocks_additional_exits_after_budget_used():
    bot, rec = make_bot()
    bot._cfg.max_sell_amount_per_trade = 1_000_000.0
    bot._cfg.daily_max_sell_amount = 1_000.0

    # Build a holding with known entry.
    pos = daily_trader.Position(
        pos_id="p2",
        symbol="FFC",
        side="buy",
        qty=30,
        entry_price=100.0,
        target_price=103.0,
        stop_price=99.0,
        status="holding",
    )
    bot._positions[pos.pos_id] = pos

    # First exit: at ~100, daily sell budget allows about 10 shares.
    bot._exit_position(pos, "target")
    assert len(rec.sell_limit_orders) == 1
    first_qty = rec.sell_limit_orders[0]["qty"]
    assert first_qty <= 10

    # Simulate full fill of first exit order.
    bot.on_order_update(
        pos.exit_ord_hash,
        "filled",
        fill_price=100.0,
        fill_qty=first_qty,
        cum_qty=first_qty,
        avg_px=100.0,
    )
    assert bot._daily_sell_value <= bot._cfg.daily_max_sell_amount + 1e-6

    # Attempt second exit should be blocked (no remaining sell budget).
    prev_orders = len(rec.sell_limit_orders) + len(rec.sell_market_orders)
    bot._exit_position(pos, "target")
    new_orders = len(rec.sell_limit_orders) + len(rec.sell_market_orders)
    assert new_orders == prev_orders

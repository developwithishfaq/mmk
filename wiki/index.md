# Wiki Index

**Last updated:** 2026-05-10

---

## Broker & API

| Page | Summary |
|------|---------|
| [authentication.md](authentication.md) | Login flow, session tokens, socket URL construction, re-login on force-disconnect |
| [broker_api.md](broker_api.md) | All REST endpoints, request/response shapes, HTTP headers |
| [socket_messages.md](socket_messages.md) | Every WebSocket message type the broker sends or receives |
| [fix_protocol.md](fix_protocol.md) | FIX tag reference, execution state machine, order status codes |
| [order_flow.md](order_flow.md) | Full lifecycle: place → ack → fill/reject → cancel, ordHash generation |

## Market

| Page | Summary |
|------|---------|
| [market_rules.md](market_rules.md) | PSX trading hours, session phases, order types allowed per phase |
| [circuit_limits.md](circuit_limits.md) | Upper/lower price circuit breakers, how to fetch and validate |

## Bot Architecture

| Page | Summary |
|------|---------|
| [daily_trader.md](daily_trader.md) | DailyTrader state machine, config params, entry/exit logic, VWAP filter |
| [known_issues.md](known_issues.md) | Bugs found and fixed, gotchas, non-obvious broker behaviours |

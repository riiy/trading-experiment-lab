# ETF setup discovery protocol

## Objective

Find one exchange-ETF setup whose one-time final validation has account CAGR at least `max(7%, 000300.SH price-index CAGR + 3 percentage points)` and maximum account drawdown no greater than 10%. `000300.SH` is the CSI 300 **price index**, never the total-return index.

This is an active, user-authorized ETF hypothesis-discovery protocol. It is
not a setup pre-registration and does not authorize account simulation, ticket
generation, or trading.

## Fixed samples and common assumptions

- Development: 2016-07-17 through 2022-07-15. It may reject candidates but cannot produce a pass conclusion.
- Final validation: 2022-07-18 through 2026-07-17. It remains unread until one candidate is promoted under the registry gate.
- One position only; initial capital 30,000 CNY; 100-share lots; maximum planned loss per trade 500 CNY; monthly loss 1,500 CNY; total drawdown freeze 3,000 CNY.
- Costs are 3 bp commission per side (minimum 5 CNY), 5 bp slippage per side, and zero ETF sell stamp duty.
- Signal structure uses qfq prices; fills, stops, marks, and costs use raw prices. Rows with an unknown raw/qfq mapping are ineligible.
- A candidate must have a point-in-time ETF listing/delisting universe, at least 120 prior trading observations, and 20-day average daily amount of at least 50 million CNY. Cash is the only defensive asset.

## Candidate families

Only the following three fixed hypotheses are eligible for development diagnostics. No parameter grid or additional rule family may be introduced after inspecting the final sample.

| ID | Entry | Exit and risk control |
|---|---|---|
| `ETF_MOMENTUM_MONTHLY_v0` | On the first trading day after each month-end, buy the eligible ETF with highest 120-day qfq return only when it is positive and above its 100-day qfq moving average. | Raw 5% initial stop; otherwise exit at the first trading day after the next month-end when its qualifying condition fails or it is no longer rank 1. |
| `ETF_TREND_BREAKOUT_v0` | Buy next open after an eligible ETF closes above its preceding 60-day qfq high while `000300.SH` is above its 200-day qfq moving average. Select the strongest 120-day return if signals collide. | Raw 3 x ATR(20) initial stop; raw 20-day low close exit scheduled for next open; 40-trading-day maximum hold. |
| `ETF_TREND_PULLBACK_v0` | Buy next open when an eligible ETF is above its 100-day qfq average and closes back above its 20-day qfq average after a pullback, provided its 120-day return is positive. Select the strongest 120-day return if signals collide. | Raw 2 x ATR(20) initial stop; raw 50-day qfq moving-average close exit scheduled for next open; 40-trading-day maximum hold. |

## Promotion and failure rules

Before any candidate is promoted, the development diagnostic must use the unified daily-account engine and report the complete cash-inclusive equity curve, costs, rejected trades, benchmark alignment, CAGR and drawdown. A candidate is rejected if it does not meet the stated account return/drawdown requirements in development, causes a drawdown freeze, has unevaluable fills, or relies on incomplete point-in-time universe data.

A successful development diagnostic is still not a pass. It can only request the registry's promotion gate; the one-time final validation then decides whether a non-tradable research setup exists.

## Development-screen record

The fixed candidate families were screened only on the development window. These
are deliberately optimistic, non-account preliminary calculations: they omit
costs, conservative fill exclusions, stop modelling, and the 30,000-CNY
ledger. A candidate that fails here is rejected; a candidate that passes would
still require the full fail-closed account diagnostic.

| Candidate | Development screen result | Decision |
|---|---:|---|
| `ETF_MOMENTUM_MONTHLY_v0` | CAGR 2.4%; maximum drawdown 18.9% | REJECT |
| `ETF_TREND_BREAKOUT_v0` | CAGR -0.3%; maximum drawdown 28.8% | REJECT |
| `ETF_TREND_PULLBACK_v0` | CAGR 1.6%; maximum drawdown 27.8% | REJECT |

The separate fixed-wide-index 200-day trend baseline also failed (best maximum
drawdown among the tested liquid broad ETFs: 15.9%). No candidate is eligible
for account-level development diagnosis or final validation.

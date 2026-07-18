# ENGINE_REMEDIATION_A_SHARE_EXECUTION_v1

## Purpose

Repair A-share price-limit data semantics and open-execution rules invalidated by `AUDIT_STOCK_RS_PULLBACK_v1_PLAN_v1`. This task changes data and execution infrastructure only. It is not a new Setup and must not change `STOCK_RS_PULLBACK_v1` signal, stop, target, holding-period, or cost rules.

## Frozen Audit Evidence

```yaml
audit_decision: ENGINE_ERROR_FOUND
audit_locked_commit: 1cbfa676459e31075c479826cb68dc58b3beeec8
engine_remediation_baseline_commit: 1cbfa676459e31075c479826cb68dc58b3beeec8
original_validation:
  preserved: true
  status: INVALIDATED_BY_ENGINE_ERROR
full_recalculation_performed: false
```

Frozen artifacts:

- `diagnostics/STOCK_RS_PULLBACK_v1/AUDIT_STOCK_RS_PULLBACK_v1.md`
- `diagnostics/STOCK_RS_PULLBACK_v1/STOCK_RS_PULLBACK_v1_audit_samples.csv`
- `diagnostics/STOCK_RS_PULLBACK_v1/STOCK_RS_PULLBACK_v1_audit_details.csv`
- `diagnostics/STOCK_RS_PULLBACK_v1/STOCK_RS_PULLBACK_v1_audit_manifest.json`
- `diagnostics/STOCK_RS_PULLBACK_v1/STOCK_RS_PULLBACK_v1_audit_summary.json`

Original validation report, metrics, signals, and trades remain immutable. Remediation outputs must use new paths and must not overwrite them.

## Confirmed Errors

1. Daily close-limit state is incorrectly used as next-open fillability. `close_at_limit_up` does not imply `one_price_limit_up` or `can_buy_at_open = false`.
2. Fixed `pct_chg >= 9.8` and `pct_chg <= -9.8` flags ignore board, trade date, historical ST status, listing phase, and rule changes.
3. Adjusted-only bars cannot independently validate actual execution prices or statutory price limits.

## Required Semantics

Replace overloaded `is_limit_up` execution usage with explicit fields:

```text
limit_up_price
limit_down_price
close_at_limit_up
close_at_limit_down
open_at_limit_up
open_at_limit_down
one_price_limit_up
one_price_limit_down
can_buy_at_open
can_sell_at_open
can_sell_intraday
can_sell_at_close
limit_rule_status
```

Rule results must support `TRUE`, `FALSE`, and `UNKNOWN`. Missing point-in-time ST status must produce `UNKNOWN_MISSING_HISTORICAL_ST`, not an assumed non-ST result.

Open-entry execution may use only trade availability, valid raw open price, and open-time fillability. It must not infer open fillability from close price.

If raw open equals an adverse price limit, daily OHLC showing a later intraday release is insufficient to prove opening-auction execution. `opening_auction_fill_status` must provide independent evidence; otherwise open fillability remains `UNKNOWN`.

Scheduled close exits use a frozen conservative rule:

```text
close above limit-down -> fill at raw close
close at limit-down -> assume unfilled and carry sell order forward
next normal, non-one-price-limit-down open -> fill at raw open
suspended or one-price-limit-down open -> continue carrying forward
```

This applies uniformly to D5, D10, and any future scheduled-close exit. It changes execution simulation only, not strategy timing conditions.

Missing historical ST uses branch invariance. Evaluate ST and non-ST rules independently. Equal execution outcomes produce `PASS_BRANCH_INVARIANT`; only divergent execution outcomes remain `NOT_EVALUABLE_MISSING_HISTORICAL_ST`.

## Market Rules Module

Planned modules:

```text
src/texperiment/market_rules/a_share_board.py
src/texperiment/market_rules/price_limit.py
src/texperiment/market_rules/listing_phase.py
src/texperiment/market_rules/historical_status.py
```

Minimum inputs:

```text
code
trade_date
board
historical_st_status
listing_date
listing_trading_day
previous_unadjusted_close
unadjusted_open
unadjusted_high
unadjusted_low
unadjusted_close
```

## Price Layers

Canonical remediation data must separate:

```text
raw_open / raw_high / raw_low / raw_close
adj_open / adj_high / adj_low / adj_close
adj_factor
adj_offset
hfq_open / hfq_high / hfq_low / hfq_close
```

TDX export convention is validated as an affine mapping: `adj_price = raw_price * adj_factor + adj_offset`. All four OHLC pairs must satisfy this mapping within tick-derived rounding tolerance. The paired hfq layer provides an independent fit check; inconsistent or unidentified mappings are blocking.

- Signals and continuous returns use `adj_*`.
- Fills, T+1 checks, stops, and statutory price limits use `raw_*`.
- Structural levels crossing corporate-action dates use `adj_factor` and `adj_offset` for explicit mapping.
- Missing raw prices or adjustment factors must not yield execution-realism PASS.

## Required Regression Cases

These audited cases must not be rejected only because the daily bar later closed or traded at a limit:

```text
600221.SH  2010-02-04
601059.SH  2023-08-04
600229.SH  2015-05-14
300039.SZ  2022-02-21
000034.SZ  2015-11-10
```

Additional fixtures must cover one-price limit-up, limit-up open that later opens, normal open followed by limit-up close, board-specific limits, rule transitions, unknown historical ST, special listing phases, missing raw prices, and adjusted-only data.

## Gates

Before any full recalculation:

1. Implement independent board, listing-phase, historical-status, and price-limit rules.
2. Obtain and validate raw prices plus adjustment factors.
3. Pass regression tests, including all five audited cases.
4. Freeze engine commit, input SHA256 values, original Setup config SHA256, date range, providers, and execution-rule version.
5. Record unresolved `UNKNOWN` coverage; blocking unknowns prohibit validation PASS.

Until all gates pass, the following remain forbidden:

```text
failure diagnostics
new Setup development
account simulation
ticket generation
full recalculation
```

## Recalculation Contract

The only permitted future output name is `STOCK_RS_PULLBACK_v1_RECALCULATED`. Do not use `v1.1` and do not overwrite original outputs.

Frozen strategy rules:

```text
strength definition
pullback definition
trigger condition
structural stop
2R target
D5/D10 exits
round-trip cost
```

Only execution semantics, price-limit rules, raw-price inputs, adjustment factors, and historical status may change.

Post-recalculation decisions:

```text
failed core metrics -> CONFIRMED_FAILED_ARCHIVED
edge metrics -> EDGE_NOT_TRADABLE
passed metrics -> independent audit and data-realism validation required
```

No recalculation outcome grants trading permission automatically.

# AUDIT_STOCK_RS_PULLBACK_v1_PLAN_v1

## Frozen Scope

```yaml
audit_plan_version: AUDIT_STOCK_RS_PULLBACK_v1_PLAN_v1
random_seed: 20260717
timezone: Asia/Shanghai
setup_id: STOCK_RS_PULLBACK_v1
setup_status: FAILED_ARCHIVED
trading_allowed: false
account_simulation_allowed: false
ticket_generation_allowed: false
```

Only audit preparation, deterministic sampling, trade reconstruction, review recording, and reporting are allowed. Failure diagnostics, MFE/MAE studies, fixed-holding studies, random controls, new Setup definitions, account simulation, ticket generation, and full recalculation are forbidden.

## Frozen Inputs

Manifest records SHA256, row/column counts, date range, code count, `source`, `adj_type`, duplicate primary keys, missing critical fields, Python version, OS, `uv.lock` SHA256, config hash, backtest engine hash, Git commit, and Git dirty state.

## Mutually Exclusive Sample

Selection order is fixed:

1. Three highest valid `net_return` rows.
2. Two lowest remaining valid `net_return` rows.
3. From remaining valid rows: 12 `stop_loss`, 10 `target_2r`, 10 `time_stop_no_upside_progress`, 8 `max_holding_exit`.
4. Five invalid rows.

Ties use `net_return`, `exit_date`, `code`, `trade_id`, then `source_trade_row`. Seeded random strata use SHA256 ranking from seed, category, and canonical source-row hash. Output must contain exactly 50 unique source rows.

## Audit Detail Schema

```text
trade_id
check_id
check_name
severity
recorded_value
recalculated_value
difference
verdict
blocking
evidence
reviewer
reviewed_at
notes
```

Core checks cover signal-day Universe, prefix-only indicators, date order, next-open entry, executability, T+1, stop-first ambiguity, gap stop, D5/D10 boundaries, gross/net/R recalculation, invalid reasons, adjustment consistency, raw execution realism, historical ST status, and board/date-specific price limits.

## Severity and Decisions

Critical failures include lookahead, date mismatch, adjusted/unadjusted mixing, impossible fills, T+1 violations, exit offsets, and duplicated/missing cost. Missing raw prices, adjustment factors, historical ST, or precise limit-price information are blocking data limitations. Missing industry/name is non-blocking.

Final decision is emitted only after all 50 rows receive manual review:

```text
ENGINE_ERROR_FOUND
AUDIT_INCONCLUSIVE_DATA_LIMITATION
AUDIT_PASSED
```

`AUDIT_PASSED` only means sampled engine behavior is credible. It never changes `FAILED_ARCHIVED` or any permission flag. `ENGINE_ERROR_FOUND` preserves original results and does not trigger automatic recalculation.

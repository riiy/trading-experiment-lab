# REMEDIATION_AUDIT_STOCK_RS_PULLBACK_v1

Decision: **REMEDIATION_AUDIT_PASSED**

仅重放锁定50个 signal_id；未运行全量回测，未修复历史 ST，未覆盖任何原始产物。

## Outcomes

- `decision`: `REMEDIATION_AUDIT_PASSED`
- `sample_count`: `50`
- `original_limit_up_invalid_samples`: `5`
- `original_limit_up_errors_resolved`: `5`
- `remediated_valid_trades`: `50`
- `data_limited_trade_outcomes`: `0`
- `unexpected_invalid_outcomes`: `0`
- `critical_failures`: `0`
- `critical_engine_error_remaining`: `False`
- `check_not_evaluable_count`: `56`
- `material_blocking_trade_count`: `0`
- `blocking_not_evaluable`: `0`
- `historical_st_repaired`: `False`
- `full_recalculation_performed`: `False`
- `new_setup_started`: `False`
- `daily_ratio_fallback_codes`: `['600114.SH']`
- `historical_st_point_overrides`: `2`

## Data-limited outcomes

- None

## Conservative close-limit execution

- `300137.SZ`: D5 scheduled close 2015-05-28 at non-ST limit-down 22.71; assumed unfilled; sold 2015-05-29 open at 22.37.
- `600037.SH`: D5 scheduled close 2015-05-28 at non-ST limit-down 36.08; assumed unfilled; sold 2015-05-29 open at 36.05.
- `600114.SH`: daily raw/qfq ratio fallback resolved flat-bar mapping; valid target exit retained.

`check_not_evaluable_count=56` counts audit checks, not blocked trades. These checks concern exact historical limit/ST branches outside material execution points. Complete fail-closed replay produced `material_blocking_trade_count=0`.

## Check verdicts

- `NOT_EVALUABLE_LIMIT_PRICE`: 6
- `NOT_EVALUABLE_LIMIT_PRICE_BRANCH_INVARIANT`: 44
- `NOT_EVALUABLE_MISSING_HISTORICAL_ST`: 6
- `PASS`: 700
- `PASS_BRANCH_INVARIANT`: 44

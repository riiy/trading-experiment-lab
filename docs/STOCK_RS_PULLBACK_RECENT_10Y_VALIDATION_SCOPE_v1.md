# STOCK_RS_PULLBACK Recent Ten-Year Validation Scope v1

## Frozen Scope

```yaml
validation_start_date: 2016-07-17
validation_end_date: 2026-07-17
indicator_warmup_trading_days: 60
indicator_warmup_start_date: 2016-04-20
account_simulation_scope: valid trades whose signal_date is in the validation window
```

The date range is fixed to the frozen market-data end date. It is not a rolling
window. The warmup boundary is the 60th local benchmark trading day before the
validation start date. Warmup data from `2016-04-20` through `2016-07-15` may
be read only to form indicators and state. It cannot produce signals, trades,
metrics, or account-simulation outcomes. A code with fewer usable warmup rows
does not borrow older observations; its indicators remain unavailable until it
accumulates enough data inside the frozen range.

## Data-Quality Exclusion

Twenty-one codes have at least one raw/qfq mapping-ambiguous row inside the
validation window. Every date for those codes is excluded from the validation
Universe with the machine-readable reason
`data_quality_raw_qfq_mapping_ambiguity`. This avoids stitching a time series
around a missing date or allowing an ambiguous raw execution mapping.

The list was frozen from
`STOCK_RS_PULLBACK_v1_MAPPING_UNEVALUABLE_DIAGNOSTICS_v1`, not from signal,
trade, or return outcomes. It is configuration data, not a security-specific
production-code branch.

## Non-Authorization

This scope amendment does not publish a paired candidate, authorize a formal
Manifest or recalculation, update the strategy decision, permit account
simulation, or enable trading.

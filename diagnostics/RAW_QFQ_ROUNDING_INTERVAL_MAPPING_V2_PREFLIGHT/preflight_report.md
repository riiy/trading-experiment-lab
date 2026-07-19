# Raw/QFQ Rounding Interval Mapping V2 Preflight

```yaml
implementation_commit: 7840726
source_diagnostics_commit: 6ba6aec
target_rows: 3306
interval_feasible_rows: 3306
uniquely_identified_rows: 0
set_valued_rows: 3306
affine_feasible_rows: 3295
ratio_feasible_rows: 2533
unbounded_affine_slope_rows: 90
execution_referenced_rows: 0
branch_invariant_rows: 0
materially_ambiguous_rows: 0
price_values_transformed: false
rows_silently_dropped: 0
global_tolerance_changed: false
security_specific_hardcodes: 0
```

The preflight uses the committed V2 solver against the frozen 3,306 row-level
diagnostics evidence. Every row has at least one interval-compatible model, but
none has an identified singleton parameter. No row was passed into an execution
branch evaluator.

The earlier V1 diagnostics categorized some rows as ratio-only because its
affine interval helper rejected an unbounded slope interval. V2 retains such
sets as affine-feasible but maps them to a conservative raw range of
`[0, +inf)` for execution. This changes no source price and does not authorize
candidate generation.

This preflight is not an implementation audit. The next permitted work is to
connect the full feasible-set representation to the execution branch evaluator,
then audit that integration before regenerating a paired candidate.

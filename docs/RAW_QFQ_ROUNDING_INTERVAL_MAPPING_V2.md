# Raw/QFQ Rounding Interval Mapping V2

## Scope

This module handles only rows that failed the existing exact affine and daily
ratio mapping checks. It does not alter stored raw or qfq prices, global
tolerances, paired filtering, setup rules, or previously evaluable rows.

Each two-decimal displayed price is interpreted as a half-open interval:

```text
p -> [p - 0.005, p + 0.005)
```

The solver records every compatible positive affine model (`qfq = a * raw +
b`) and, separately, every compatible positive ratio model (`qfq = a * raw`).

## Determinism And Execution

`MappingFeasibleSet` persists a deterministic representative using
`SLOPE_INTERVAL_MIDPOINT_INTERCEPT_SLICE_MIDPOINT_V1`. This representative is
for serialization, hashing, and diagnostics only. It is never evidence that a
single execution path is valid.

Execution must call `evaluate_mapping_branch_invariance()` with an evaluator
that returns all materially possible outcomes over the full conservative raw
price ranges. One outcome yields `PASS_MAPPING_BRANCH_INVARIANT`; zero or more
than one yields a fail-closed status.

An affine feasible set may have no finite slope upper bound. The module then
reports `[0, +inf)` for every derived raw execution price. It never samples the
canonical point to make that range look finite.

## Statuses

- `PASS_EXACT_AFFINE` and `PASS_EXACT_DAILY_RATIO` remain owned by the existing mapper.
- `PASS_ROUNDING_INTERVAL_IDENTIFIED` and `PASS_ROUNDING_INTERVAL_SET` describe interval solvability.
- `PASS_MAPPING_BRANCH_INVARIANT` is required before an ambiguous mapping can support execution.
- `NOT_EVALUABLE_MAPPING_AMBIGUITY` and `SOURCE_MAPPING_INCONSISTENT` block use.

HFQ may be supplied later as a separately audited constraint. It is not used by
this module to select a raw/qfq mapping parameter.

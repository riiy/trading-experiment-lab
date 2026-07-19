# STOCK_RS_PULLBACK_v1 Core Input Pair Generation 2

## Decision

```yaml
decision: CORE_INPUT_PAIR_VALIDATION_FAILED
blocking_error: RAW_QFQ_MAPPING_NOT_EVALUABLE
candidate_published: false
formal_manifest_generated: false
full_recalculation_performed: false
trading_allowed: false
```

## Frozen Sources

The run read the complete TDX text-export snapshots for raw, qfq, and hfq data.
All three layers contained 5,534 A-share files. Raw and qfq each contained
16,666,488 source rows with identical primary-key sets.

```yaml
raw_tree_sha256: ef35d691c95c92f272903948e505a6ed4bef72a1234c055aac377224756c773a
qfq_tree_sha256: 91da5169adb2caa234c3be98183eee5adb4cd84763c1ae539d3bf6abfadeafe1
hfq_tree_sha256: f0a055c17cdbc7da6d22c1e2dc0a5c9a41b335d8966968c76aaf6d9450d67919
raw_only_keys: 0
qfq_only_keys: 0
duplicate_source_keys: 0
volume_mismatch_rows: 0
```

## Paired Filtering

The audited `PAIRED_NON_POSITIVE_OHLC_FILTER_V1` rule synchronously removed a
key from both candidate outputs whenever raw, qfq, or hfq had missing or
non-positive OHLC. No prices were transformed.

```yaml
synchronously_dropped_ohlc_keys: 740778
synchronously_dropped_unsafe_pre_close_rows: 595
fully_filtered_code_count: 8
price_values_transformed: false
```

## Blocking Result

After paired filtering, 15,922,404 mappings were evaluable and 3,306 were not.
The fail-closed mapping contract requires every retained row to be evaluable,
so the candidate was rejected.

```yaml
mapping_method: AFFINE_THEN_DAILY_RATIO_FALLBACK
evaluable_rows: 15922404
unevaluable_rows: 3306
evaluable_ratio: 0.9997924111389697
```

The transactional temporary directory was removed. The requested final output
directory was never created. This is a core-input data validation result, not a
strategy validation result.

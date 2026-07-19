# Mapping Unevaluable Diagnostics v1

Diagnostics-only replay of the frozen pairing filter and mapping implementation.
No production file, mapping tolerance, paired output, or strategy rule was modified.

```yaml
decision: MAPPING_DIAGNOSTICS_MIXED_DETERMINISTIC
retained_rows: 15925710
unevaluable_rows: 3306
affected_codes: 261
flat_ohlc_rows: 3306
raw_flat_ohlc_rows: 0
qfq_flat_ohlc_rows: 3306
low_price_qfq_rows: 2599
corporate_action_adjacent_rows: 1826
```

Every failure has a flat qfq OHLC display. Interval feasibility proves that a displayed-price-consistent map exists, but it does not select one unique affine parameter pair. No inheritance rule was applied.
This is diagnostics evidence only. A future mapping rule must be separately specified, implemented, and audited before any candidate can be regenerated.

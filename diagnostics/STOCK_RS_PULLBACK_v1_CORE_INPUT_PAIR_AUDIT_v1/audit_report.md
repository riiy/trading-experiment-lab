# Core Input Pair Audit V1

This is an independent, read-only audit of
`STOCK_RS_PULLBACK_v1_recent_10y_pair_v3`.

It verifies the frozen global warmup and validation range, pair key/order and
volume consistency, adjustment identity, positive finite OHLC values, absence
of all 21 excluded codes, output hashes, zero unevaluable mappings, and closed
formal-input/recalculation/trading permissions.

The audit may establish that the candidate is eligible for a later formal input
freeze. It does not itself freeze input data, authorize a recalculation, or
produce a strategy decision.

## Decision

`CORE_INPUT_PAIR_AUDIT_PASSED`

The independent scan verified 10,249,283 raw and qfq rows from `2016-04-20`
through `2026-07-17`, with 5,505 codes. Both candidate file hashes match the
candidate audit. There were zero violations for primary-key/order consistency,
adjustment identity, OHLC validity, present `pre_close` validity, volume
consistency, date range, configured code exclusions, or mapping evaluability.

Formal input publication, formal recalculation, strategy decision publication,
and trading remain disabled.

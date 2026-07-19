# FULL_PIPELINE_RECALCULATION_IMPLEMENTATION_AUDIT_v2_REAUDIT_1

## Decision

```yaml
decision: IMPLEMENTATION_ERROR_FOUND
implementation_commit: 4802d8303e5a60e393becfafc961c686b9144e49
implementation_audited: false
full_recalculation_allowed: false
trading_allowed: false
```

## Prior Findings

Both prior blocking findings are verified fixed. The synthetic authorized run performs
an fsync-backed atomic publication into a read-only final tree, and the persisted stage
records reconstruct an eight-stage producer/consumer hash chain that rejects metadata or
artifact tampering.

## New Blocking Finding

`InputSnapshotStage` iterates over `comparison_only_inputs`, requires each archived
original file to exist, hashes it, and registers it as an `INPUT_SNAPSHOT` output. This
violates the frozen contract that original signals, trades, and metrics may be read only
by `DELTA_AND_DECISION`.

Consequently, deleting or drifting original signals aborts at `INPUT_SNAPSHOT`; the new
Universe, indicators, signals, and trades cannot complete independently before Delta
fails closed. Old strategy outputs therefore affect an upstream stage even though they
do not alter the calculated market artifacts.

## Scope

Preflight passed and the complete 193-test suite passed. The eight-stage fixture,
publication, durable-chain, execution regression, and tamper tests executed. The audit
then stopped on the forbidden-input boundary failure. Remaining double-publication,
dynamic perturbation, and fsync-injection cases are not evaluated and are not inherited
as passed.

No production code, Setup configuration, Registry strategy status, formal recalculation
output, account simulation, ticket, or authoritative strategy decision was changed or
generated.

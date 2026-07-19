# FULL_PIPELINE_RECALCULATION_IMPLEMENTATION_v2

## 1. Purpose

`FULL_PIPELINE_RECALCULATION_IMPLEMENTATION_v2` builds an immutable, full-pipeline
recalculation runner for `STOCK_RS_PULLBACK_v1`. It is execution infrastructure, not a
new Setup and not a strategy-rule change.

Current implementation state:

```yaml
status: implementation_error_found
baseline_commit: 468bacc6fead27020e2dfce5f33368a623492122
implementation_frozen: false
implementation_audited: false
implementation_audit_decision: IMPLEMENTATION_ERROR_FOUND
remediation_1: completed_pending_reaudit
reaudit_1: IMPLEMENTATION_ERROR_FOUND
blocking_finding: COMPARISON_ONLY_INPUT_LEAKED_INTO_INPUT_SNAPSHOT
full_recalculation_allowed: false
full_recalculation_performed: false
```

Implementation progress after the contract baseline:

```yaml
upstream_stages_implemented:
  - INPUT_SNAPSHOT
  - MARKET_STATE_REBUILD
  - UNIVERSE_REBUILD
  - INDICATOR_REBUILD
downstream_stages_implemented:
  - SIGNAL_REBUILD
  - TRADE_REBUILD
  - METRICS_REBUILD
  - DELTA_AND_DECISION
concrete_stages_implemented: true
implementation_audited: false
full_recalculation_allowed: false
```

All eight stages are development-tested. The complete runner remains unaudited and
cannot be frozen or authorized until the independent implementation audit passes.

Remediation 1 adds a verified persistent artifact DAG and an fsync-backed atomic move
from the temporary run root to a read-only final root. Successful formal execution now
requires all eight stage records, closed downstream permissions, matching on-disk hashes,
and an absent final target. These changes require a new audit from the beginning.

Trading, account simulation, and ticket generation remain disabled throughout this
engineering phase. No strategy decision may be produced before the implementation audit
passes and a new engine commit is frozen. Development runs may emit a non-authoritative
decision preview; they cannot publish it or update the Registry setup decision.

## 2. Contract

Contract ID:

```text
FULL_PIPELINE_RECALCULATION_V2
```

The runner must execute exactly these stages, in order:

```text
INPUT_SNAPSHOT
MARKET_STATE_REBUILD
UNIVERSE_REBUILD
INDICATOR_REBUILD
SIGNAL_REBUILD
TRADE_REBUILD
METRICS_REBUILD
DELTA_AND_DECISION
```

The production pipeline starts from frozen raw, qfq, benchmark, historical-status,
Setup, and cost inputs. It must rebuild market state, Universe, indicators, signals,
trades, and metrics by calling the existing domain implementations. The runner must not
copy or reimplement strategy rules.

Original signals, trades, and metrics are comparison inputs for
`DELTA_AND_DECISION` only. They are forbidden inputs to every earlier stage:

```text
data/signals/STOCK_RS_PULLBACK_v1_signals.csv
data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv
data/reports/STOCK_RS_PULLBACK_v1_metrics.json
```

## 3. Stage Responsibilities

### INPUT_SNAPSHOT

Validate the frozen commit, clean worktree, Manifest schema, input existence and hashes,
raw/qfq alignment, date ranges, and absence of the final output directory. Any mismatch
aborts before data processing.

### MARKET_STATE_REBUILD

Rebuild raw/qfq mapping, boards, suspensions, price limits, open/close/one-price states,
historical ST branches, and buy/sell fillability. Preserve branch invariance, fail-closed
unknowns, and conservative close-limit carry-forward behavior.

### UNIVERSE_REBUILD

Call the existing A-share Universe implementation against the rebuilt daily data. Apply
the frozen non-ST, listing-age, liquidity, one-lot, suspension, and limit constraints.
Do not read the old Universe.

### INDICATOR_REBUILD

Call the existing indicator implementation to rebuild MA20, MA60, 20-day stock and
benchmark returns, excess return, 10-day high, drawdown, and volume MA5. Do not read the
old indicator artifact.

### SIGNAL_REBUILD

Call the existing `STOCK_RS_PULLBACK_v1` signal implementation using the newly rebuilt
Universe and indicators. Do not read original signals.

### TRADE_REBUILD

Call the audited backtest implementation using newly rebuilt signals and market state.
Preserve next-open entry, T+1, structural stop, 2R target, stop-first ambiguity handling,
D5/D10 exits, close-limit carry-forward, and one round-trip cost deduction.

### METRICS_REBUILD

Call the existing validation implementation for core metrics, yearly results, exit
reasons, and invalid reasons. Missing industry metadata remains non-blocking for the core
decision.

### DELTA_AND_DECISION

Only this stage may read original signals, trades, and metrics. It compares signal sets,
trade outcomes, execution changes, blocking data, and validation metrics, then applies
the frozen decision policy.

## 4. Manifest V2

The Manifest is validated by `texperiment.full_recalculation.schema`. It requires:

- contract ID and `Asia/Shanghai` timezone;
- repository commit and clean state;
- disabled trading, account simulation, and ticket permissions;
- source/output Setup IDs, config hash, and `rules_changed: false`;
- profiled raw, qfq, and benchmark inputs with hashes, rows, dates, and code counts;
- hashes for ST overrides, Setup config, and cost config;
- execution, price-limit, ST-branch, close-carry, raw/qfq mapping, and cost versions;
- the exact forbidden comparison inputs and exact ordered stage list.

Manifest freeze and production execution remain blocked while
`full_recalculation_allowed` is false. After freeze, any code, Git, or input drift aborts
the attempt.

## 5. Artifacts and Publication

Work is staged under:

```text
data/recalculations/.tmp/<run_id>/
```

Only a completely successful run may be atomically published to:

```text
data/recalculations/STOCK_RS_PULLBACK_v1_RECALCULATED/<run_id>/
```

Existing final directories are never overwritten. Each stage records status, timestamps,
input/output hashes, row/date/code profiles, warnings, and blocking errors. Failed runs
publish diagnostics only under `diagnostics/recalculation_attempts/<run_id>/`; temporary
data must never resemble a formal strategy result.

## 6. Abort and Decision Safety

Machine abort states are:

```text
RECALCULATION_ABORTED_PIPELINE_CONTRACT_MISMATCH
RECALCULATION_ABORTED_INPUT_DRIFT
RECALCULATION_ABORTED_DIRTY_WORKTREE
RECALCULATION_ABORTED_OUTPUT_EXISTS
RECALCULATION_ABORTED_STAGE_FAILURE
RECALCULATION_INCONCLUSIVE_DATA_LIMITATION
```

None may produce a strategy PASS, EDGE, or FAIL decision. Legacy signal execution replay
is an audit utility with `run_type: SIGNAL_EXECUTION_REPLAY` and
`strategy_validation_decision_allowed: false`.

## 7. Implementation Audit Gate

After implementation tests pass, run a separate
`FULL_PIPELINE_RECALCULATION_IMPLEMENTATION_AUDIT_v2`. It must verify input isolation,
actual Universe/indicator regeneration, complete artifact hash chaining, atomic
publication, original-artifact immutability, closed permissions, deterministic synthetic
results, and replay decision blocking.

Only `IMPLEMENTATION_AUDIT_PASSED` followed by a frozen new engine commit may change the
system to `recalculation_authorized` and `full_recalculation_allowed: true`. That state
still does not enable trading.

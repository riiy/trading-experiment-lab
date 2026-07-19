# Recent Ten-Year Validation Scope Audit V1

## Decision

`RECENT_10Y_VALIDATION_SCOPE_AUDIT_PASSED`

This audit freezes the validation scope only. It is not a raw/qfq mapping audit,
does not publish a formal input snapshot, does not execute a recalculation, and
does not establish a strategy-performance conclusion.

## Frozen Scope

- Validation window: `2016-07-17` through `2026-07-17`, inclusive.
- Indicator warmup: 60 trading days before the reporting window.
- Data-quality exclusion: 21 codes across the whole validation scope, rather
  than isolated ambiguous rows. This preserves per-code indicator and execution
  continuity.

## Evidence

The frozen mapping diagnostics contain 3,306 non-exactly-mappable rows. Of
these, 30 rows fall inside the fixed recent-ten-year window and they belong to
exactly the 21 configured excluded codes. The configuration list exactly matches
that derived set.

Machine-readable evidence: `audit_manifest.json`.

## Acceptance Checks

- Fixed window and 60-day warmup are present in the setup configuration.
- Configured exclusion codes match the in-window diagnostics exactly.
- No paired formal input candidate exists.
- No formal recalculation manifest exists in the expected output namespace.
- Full local test suite and `texperiment config-check` passed.
- Trading, account simulation, ticket generation, formal recalculation, and
  strategy-decision publication remain disabled.

## Follow-On Gate

The next data task is to build and independently validate a new paired raw/qfq
candidate for this frozen window and code universe. It must not be treated as a
formal input until that pair validation passes.

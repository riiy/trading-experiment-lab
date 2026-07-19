# STOCK_RS_PULLBACK_v1 Core Input Pair Remediation Audit 1

## Decision

`DATA_PAIR_IMPLEMENTATION_ERROR_FOUND`

Implementation `84315e86ee48cc302a3c0512a988fb30adb0e7f1` is not eligible to publish a formal core-input pair.

## Blocking Findings

1. `FILTERED_SOURCE_CLOSE_LEAKS_INTO_RETAINED_RAW_PRE_CLOSE`

   The synchronous filter removes a key when raw OHLC is non-positive, but computes `raw_pre_close` before filtering. A retained next-day row can therefore contain the rejected non-positive raw close. The reproduced retained row had `raw_pre_close=-1.0`. This value feeds price-limit execution semantics.

2. `PUBLICATION_FAILURE_DIAGNOSTIC_FALSE_POSITIVE`

   `candidate_published` is set before atomic rename. Injecting a rename failure correctly leaves no final directory and cleans the temporary directory, but the durable diagnostic incorrectly records `candidate_published=true`.

## Verified Behavior

- Source key mismatches fail closed.
- Non-positive OHLC in raw, qfq, or hfq synchronously removes the key from both outputs.
- Retained raw and qfq OHLC values are not transformed.
- Listing date and source trading-day sequence survive filtering.
- Suspension, volume mismatch, mapping failure, duplicate keys, multi-batch verification, temporary cleanup, and pre-existing output behavior pass.
- The dedicated CLI route exists and accepts explicit raw, qfq, hfq and output paths.
- Focused tests: `16 passed`.
- Full suite: `239 passed`.
- `config-check: OK`; trading, account simulation, tickets, Manifest freeze and formal run remain disabled.

## Scope

No production code, Registry, strategy configuration, formal Manifest, formal recalculation, or full-source candidate was changed or generated during this audit.

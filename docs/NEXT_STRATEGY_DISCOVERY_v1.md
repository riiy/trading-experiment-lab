# Next Strategy Discovery V1

## Scope

This A-share hypothesis-discovery phase has been explicitly reauthorized by
user direction. It may continue the pre-registered
`VOLATILITY_CONTRACTION_BREAKOUT_v1` has been closed as a research failure.
Its development trade-level edge was too weak before account costs and the
final validation window was never run. It remains no-trade and must not
generate tickets or enable trading.

## Research Question

Which simple, execution-aware next-market hypothesis merits a single
pre-registered validation attempt on the frozen recent-ten-year data set?

## Candidate Families

Candidates must be economically distinct from relative-strength pullback:

1. Liquid-stock volatility contraction followed by range expansion.
2. Post-earnings-style gap continuation, only if a timestamped event source is
   available before the research date.
3. Liquid-stock mean reversion after a broad-market dislocation, with market
   regime and execution filters defined before testing.

The next research track is `ETF_SETUP_DISCOVERY_CYCLE_2`. No new A-share setup
is active after the VCB research failure unless separately reauthorized.

The event-based family is unavailable until a survivorship-safe, timestamped
event data source is frozen. It must not be approximated from price movement.

## Discovery Constraints

- Use the frozen 2016-07-17 to 2026-07-17 validation scope only for final
  validation. Reserve an earlier development subperiod before choosing one
  hypothesis.
- Keep raw execution prices and qfq structural prices separate.
- Do not use historical ST status in the VCB data, universe, or execution
  path. Retain ordinary A-share price-limit, suspension, T+1, cost and
  delayed-exit semantics.
- Use no more than one chosen hypothesis per pre-registration cycle.
- Do not tune thresholds against the final validation period.

## Promotion Gate

A candidate can become `NEW_SETUP_PRE_REGISTRATION_PENDING` only when it has:

- an economic rationale and falsifiable entry/exit rules;
- explicitly available data inputs and timing semantics;
- a development/validation split fixed before the first full run;
- invalid-trade and execution semantics inherited or specified;
- pre-registered thresholds and a no-trade permission snapshot.

## Exclusions

- No account simulation, ticket generation or live trading.
- No reuse of the archived setup's results as evidence for a new candidate.
- No parameter search on the final validation period.

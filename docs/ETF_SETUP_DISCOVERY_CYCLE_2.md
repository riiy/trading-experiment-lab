# ETF Setup Discovery Cycle 2

## Status

This is a research-only candidate screen. It does not create a setup, authorize
account simulation, generate tickets, or enable trading. The previous VCB setup
is archived as a research failure, and the first ETF candidate screen rejected
all three pre-registered families.

## Data Screen

The development window remains fixed at `2016-07-17` through `2022-07-15`.
Using the existing exploration parquet, 36 ETF codes have at least 120 rows,
begin before the development window, and average daily amount of at least CNY
50 million through the development end. The most liquid stable candidates
include `510300.SH`, `510500.SH`, `510050.SH`, `518880.SH`, `511990.SH`, and
`511880.SH`.

This is only a coverage screen. The source still lacks a point-in-time listing
master and ETF fillability remains `UNKNOWN`; therefore these rows cannot
support formal validation until those blockers are separately resolved.

## Candidate Hypotheses

The next screen will use a fixed, small universe of broad equity, commodity,
and cash-like ETFs. The cash-like ETF is a defensive proxy, not a guaranteed
cash substitute; its eligibility and execution must be verified before any
formal run.

1. `ETF_DEFENSIVE_ROTATION_v1`: on the first trading day after month-end,
   hold the highest 120-day qfq return among `510300.SH`, `510500.SH`, and
   `518880.SH` only when that return is positive and price is above its
   100-day qfq average; otherwise hold `511990.SH`.
2. `ETF_BENCHMARK_CASH_SWITCH_v1`: hold `510300.SH` when it is above its
   200-day qfq average and its 120-day return is positive; otherwise hold
   `511990.SH`. Evaluate only monthly decisions.
3. `ETF_RELATIVE_STRENGTH_DEFENSIVE_v1`: monthly select the strongest positive
   120-day return from `510300.SH`, `510500.SH`, and `518880.SH`, but switch to
   `511990.SH` whenever the candidate is below its 100-day qfq average.

These are discovery hypotheses, not parameter variants. No final-window data
may be read while choosing among them, and no additional family may be added
after the development screen starts.

## Next Gate

Before implementing a candidate, freeze a cycle manifest containing the exact
code list, dates, mapping exclusions, benchmark `000300.SH`, and no-trade
permissions. A candidate may proceed only if its development diagnostic uses
the account ledger with costs and reports CAGR, maximum drawdown, rejected
trades, and benchmark comparison. Failure or missing ETF execution data is a
research stop, not a reason to relax the gates.

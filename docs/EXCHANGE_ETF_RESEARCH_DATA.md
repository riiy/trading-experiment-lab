# Exchange ETF research data

> Status: **ACTIVE_NO_TRADE_BY_USER_AUTHORIZATION**. ETF exploration is limited
> to the frozen inputs stated below; account simulation, ticket generation, and
> trading remain prohibited.

## Scope

`ingest-tdx-paired-exchange-etf-daily` reads only `data/raw/etf/{qfq,raw,hfq}` and retains exchange ETF code bands. It excludes `160` and `161` linked-ETF LOFs. Selection is by exchange and code band, never by the current name.

The resulting dataset keeps adjusted OHLC for structural signals and raw OHLC for executable-price research. An affine raw-to-qfq mapping must be known for any row used in a future formal test.

## ST and executable limits

ST/PT is not an applicable status for this ETF scope. The importer records `historical_st_status=NOT_APPLICABLE_ETF`; it does not fabricate `FALSE` values. ETF-specific price-limit and fill fields deliberately remain `ETF_LIMIT_RULE_PENDING`. They must be specified and tested before any account simulation or formal validation.

## Status and limitations

This command creates an **exploration** input only. A directory containing files that all survive to its final date is not proof of a point-in-time historical ETF universe. Until a listing/delisting master and coverage audit are frozen, it must not support a final validation, account simulation, pre-registration, or trade ticket.

The benchmark for any future ETF setup is local `000300.SH`, the CSI 300 **price index**. It is not the CSI 300 total-return index; benchmark CAGR uses the account-equity start/end dates and 365.25-day annualization.

## Historical-universe source assessment

`data/raw/etf/{raw,qfq,hfq}` is the sole ETF research source. `data/raw/SH#000300.txt` is the sole permitted non-ETF input, used only as the `000300.SH` CSI 300 price-index benchmark. Its code-band import produced 1,455,560 rows for 1,645 codes, spanning 2005-02-23 through 2026-08-03, with no duplicate or missing core OHLCV rows. No code ends before 2026-07-17, so this is a surviving-instruments sample rather than a point-in-time historical ETF universe. It must not support cross-sectional historical selection, final validation, account simulation, pre-registration, or ticket generation until that limitation is resolved within an approved source.

The three price layers are not sufficient by themselves: 15,304 rows do not have a validated raw-to-qfq mapping and are excluded fail-closed from any future formal calculation.

## Frozen-source record

The current source snapshot contains 2,420 text files in each of `raw`, `qfq`, and `hfq` (7,260 files; 393,807,710 bytes). Hashing each file's layer-relative path and contents in sorted layer/path order with SHA-256 gives:

```
3fe2c7a5e6eebe32fcc41d06ace2edeb4617418fdd7bcdd08f3ce8eefc0b18b6
```

The matching derived exploration dataset passes the canonical daily-bar quality check. This fingerprint is an input identity, not an approval to use the sample for validation.

## Formal-validation blockers in the designated source

- The source records ETF opening, intraday, and closing fillability as `UNKNOWN`. A daily-bar backtest must not convert those values to executable fills without an audited ETF execution policy and its required inputs.
- No point-in-time listing/delisting universe is present, and all imported codes survive past the final-window end. Cross-sectional selection would be survivor-biased.

The permitted `data/raw/SH#000300.txt` file imports cleanly as `000300.SH`, has 5,241 rows spanning 2005-01-04 through 2026-08-03, and is unadjusted index-price data. It resolves the benchmark-CAGR input only; it does not resolve the two ETF formal-validation blockers above.

These remaining items are hard, fail-closed blockers for a formal setup; they do not turn an exploratory return calculation into a validation result.

### Execution-rule research status

Exchange rules provide preliminary support for a 10% price-limit treatment of ordinary fund/ETF trading and 100-unit order lots, but this is not enough to overwrite the source's `UNKNOWN` fillability fields. The source lacks the historical product classification and auction/order-book state needed to identify product exceptions, first-listing no-limit sessions, and one-price limit lockouts for every `(date, code)`. A formal execution policy must supply that coverage before it may create executable-entry or exit flags.

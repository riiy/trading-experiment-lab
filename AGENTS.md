# Agent Instructions

## Commands

- Use Python 3.11+ and `uv`; run project commands through `uv run`.
- Install locked dependencies with `uv sync --extra dev`; add `--extra qlib` only when working on the optional Qlib adapter.
- Install AkShare ingestion support with `uv sync --extra dev --extra akshare`.
- Validate configuration and safety gates with `uv run texperiment config-check`.
- Run all tests with `uv run pytest -q`; run focused tests with `uv run pytest tests/test_trading_guard.py -q` or `uv run pytest tests/test_trading_guard.py -k guard -q`.
- `make config-check` and `make test` are legacy equivalents using `PYTHONPATH=src`; prefer the `uv run` commands above.
- No repository lint, formatter, typecheck, CI, or pre-commit command is configured; do not invent required checks.

## Structure

- Runtime package lives under `src/texperiment`; `texperiment.cli` exposes `config-check` and `status`, and the installed `texperiment` script points to it.
- `configs/` and root `experiment_registry.yaml` are runtime inputs. `config-check` reads both account/setup YAML plus registry, then enforces trading-disabled state.
- Main flow is data, universe, indicators, signal, backtest, metrics, account simulation, ticket, then guards; corresponding code is grouped under `src/texperiment/`.
- `tests/` contains pytest tests for each major domain; `pyproject.toml` sets `src` on the test path and limits discovery to this directory.
- `docs/` contains policy, pre-registration, data, account-simulation, and ticket specifications; consult the relevant document before changing domain rules.
- A-share daily ingestion is implemented in `src/texperiment/data/`; loaders accept CSV/Parquet files or directories and write canonical data to `data/processed/a_share_daily.parquet`.

## Data Ingestion

- Use `uv run texperiment ingest-a-share-daily --input data/raw/market/a_share_daily --output data/processed/a_share_daily.parquet --provider auto --adj-type qfq` for raw A-share daily bars.
- Use `uv run texperiment fetch-a-share-daily --start-date YYYYMMDD --end-date YYYYMMDD --output data/processed/a_share_daily.parquet --adj-type qfq` for direct full-market AkShare ingestion; this makes one request per stock and reports failed symbols.
- Use `uv run texperiment ingest-tdx-a-share-daily --input /path/to/T0002/vipdoc --output data/processed/a_share_daily.parquet` for fast local TongdaXin `.day` ingestion; TDX data is unadjusted, so do not label it qfq.
- TDX CLI writes one stock file at a time to Parquet to limit memory use; preserve this streaming path for full-market imports.
- AkShare symbol discovery retries exchange-list requests and falls back to `stock_zh_a_spot_em`; historical requests also retry, but `symbols_failed` still requires review before formal research.
- Supported providers are `canonical`, `akshare`, `tushare`, and `baostock`; `auto` detects provider from input columns.
- Canonical units are `volume` in shares and `amount` in CNY. Tushare input is converted from lots/thousand CNY; AkShare volume is converted from lots.
- Canonical codes use `000001.SZ`, `600000.SH`, or `833000.BJ`; standard fields are defined by `src/texperiment/data/schema.py` and documented in `docs/DATA_DICTIONARY.md`.
- `qfq` is default for historical `STOCK_RS_PULLBACK_v1` research. Do not use adjusted research prices for future execution-level tickets.
- Check output with `uv run texperiment data-check --path data/processed/a_share_daily.parquet`; quality checks are strict by default. Use `--allow-quality-warnings` only for temporary exploration, never formal validation.
- Keep raw provider exports under `data/raw/market/a_share_daily`; read `docs/A_SHARE_DAILY_DATA_INGESTION.md` before changing ingestion rules.

## Safety Constraints

- This is a research/no-trade system. Keep `trading_allowed: false` and live-trading blocking behavior unless an explicit requirement changes the policy and its tests/configuration together.
- Current setup is `STOCK_RS_PULLBACK_v1`; current scope is A-share only despite broader allowed instrument metadata.
- Preserve account limits: 30,000 CNY capital, 500 CNY maximum planned loss per trade, 1,500 CNY monthly loss, 3,000 CNY total drawdown, and at most one position.
- Do not add behavior that enables live trading, unvalidated formal tickets, averaging down, discretionary intraday trading, or use of core-asset capital.

## Changes

- Keep setup rules synchronized across its YAML config, pre-registration document, implementation, and tests when changing behavior.
- Use `uv.lock` as dependency source of truth; update it with `uv lock` when dependency declarations change.

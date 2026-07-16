# Trading Experiment Lab

`trading-experiment-lab` is a research and risk-control project for a 30,000 CNY stock experiment account.

It is **not** an automatic trading system. It is designed to make every setup:

1. pre-registered,
2. historically validated,
3. account-simulated under a 30,000 CNY limit,
4. converted into a trade ticket only after permission gates pass,
5. archived when it fails.

Current setup:

```text
STOCK_RS_PULLBACK_v1 = 强势股相对强度回踩 Setup
```

Current status:

```text
trading_allowed = false
live_trading = forbidden
```

## Quick start

```bash
cd trading-experiment-lab
uv sync --extra dev
uv run texperiment config-check
uv run pytest -q
```

Install AkShare for direct full-market ingestion:

```bash
uv sync --extra dev --extra akshare
```


## A-share daily data ingestion

Put raw provider exports under:

```text
data/raw/market/a_share_daily/
```

Normalize them into the canonical parquet dataset:

```bash
texperiment ingest-a-share-daily \
  --input data/raw/market/a_share_daily \
  --output data/processed/a_share_daily.parquet \
  --provider auto \
  --adj-type qfq

texperiment data-check --path data/processed/a_share_daily.parquet
```

Supported input styles for the first version: canonical, AkShare-style CSV, Tushare-style CSV, and Baostock-style CSV. See `docs/A_SHARE_DAILY_DATA_INGESTION.md`.

Fetch full-market A-share daily bars directly from AkShare:

```bash
uv run texperiment fetch-a-share-daily \
  --start-date 20200101 \
  --end-date 20261231 \
  --output data/processed/a_share_daily.parquet \
  --adj-type qfq
```

This performs one historical request per stock. Failed symbols are reported and do not discard successful symbols; review `symbols_failed` before formal research.

For faster full-market ingestion from a local TongdaXin installation, point the command at its `vipdoc` directory:

```bash
uv run texperiment ingest-tdx-a-share-daily \
  --input /path/to/T0002/vipdoc \
  --output data/processed/a_share_daily.parquet
```

TongdaXin `.day` files are unadjusted (`adj_type=none`). Use AkShare or another adjusted source for qfq historical research.

## Core workflow

```text
Data
↓
Universe
↓
Indicators
↓
Signal
↓
Backtest
↓
Metrics
↓
Account Simulation
↓
Trade Ticket
↓
Guard
```

## Current rule

Before validation passes:

```text
no trade
no formal ticket
no use of the 30,000 CNY live account
```

## Qlib

The project uses a lightweight internal framework first and keeps an optional Qlib adapter placeholder.
Qlib can be introduced later for multi-factor, ML, rolling experiment, and portfolio optimization workflows.

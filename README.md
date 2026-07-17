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
none (audit planning only)
```

Current status:

```text
trading_allowed = false
live_trading = forbidden
```

`STOCK_RS_PULLBACK_v1` 已因核心验证指标失败归档，禁止账户仿真和交易票生成。当前只允许完整性审计，不定义或实现新 Setup。归档清单：`archive/setups/STOCK_RS_PULLBACK_v1/ARCHIVE_MANIFEST.md`。

## Quick start

```bash
uv sync --extra dev
uv run texperiment config-check
uv run pytest -q
```

所有项目命令通过 `uv run` 执行。旧版 `venv` / `pip` 命令不再是标准流程。

不安装项目脚本时：

```bash
uv run python -m texperiment.cli config-check
uv run pytest -q
```


## A-share daily data ingestion

Put raw provider exports under:

```text
data/raw/market/a_share_daily/
```

Normalize them into the canonical parquet dataset:

```bash
uv run texperiment ingest-a-share-daily \
  --input data/raw/market/a_share_daily \
  --output data/processed/a_share_daily.parquet \
  --provider auto \
  --adj-type qfq

uv run texperiment data-check --path data/processed/a_share_daily.parquet
```

Supported input styles for the first version: canonical, AkShare-style CSV, Tushare-style CSV, and Baostock-style CSV. See `docs/A_SHARE_DAILY_DATA_INGESTION.md`.

## Core workflow

```text
Data
↓
{ Indicators + Universe }
↘
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


## A股股票池过滤

标准化日线数据后，生成 `STOCK_RS_PULLBACK_v1` 可执行股票池：

```bash
uv run texperiment build-a-share-universe \
  --input data/processed/a_share_daily.parquet \
  --output data/processed/a_share_universe.parquet \
  --setup STOCK_RS_PULLBACK_v1 \
  --as-of 2026-07-15
```

生成用于历史信号回测的全日期股票池时，不要使用 `--as-of`，并保留拒绝行：

```bash
uv run texperiment build-a-share-universe \
  --input data/processed/a_share_daily.parquet \
  --output data/processed/a_share_universe_full.parquet \
  --setup STOCK_RS_PULLBACK_v1 \
  --include-rejected
```

调试过滤原因：

```bash
uv run texperiment build-a-share-universe \
  --input data/processed/a_share_daily.parquet \
  --output data/processed/a_share_universe_debug.parquet \
  --setup STOCK_RS_PULLBACK_v1 \
  --as-of 2026-07-15 \
  --include-rejected
```

文档：`docs/A_SHARE_UNIVERSE_FILTERING.md`。


## A股指标层

生成 `STOCK_RS_PULLBACK_v1` 所需的 MA20 / MA60 / 20日收益 / 相对沪深300强度 / 近10日高点 / 回撤幅度 / 成交量MA5：

```bash
uv run texperiment compute-a-share-indicators \
  --daily-input data/processed/a_share_daily.parquet \
  --benchmark-input data/processed/index_daily.parquet \
  --benchmark-code 000300.SH \
  --output data/processed/a_share_indicators.parquet \
  --setup STOCK_RS_PULLBACK_v1
```

`--daily-input` 必须是包含每只股票完整历史的日线文件，不能使用只含每只股票一行的 `a_share_universe.parquet`。Parquet 输入自动分批计算并流式写出，避免完整日线一次性加载导致内存不足。

默认批大小为 `250000` 行；内存较小时使用 `--batch-size 50000`。不要用 shell 重试掩盖退出码 `137`，该退出码表示进程被系统 OOM killer 终止。

如果需要从 TDX 文本导出生成沪深300基准文件：

```bash
uv run texperiment ingest-tdx-export-index-daily \
  --input data/raw/export/SH#000300.txt \
  --output data/processed/index_daily.parquet \
  --code 000300.SH
```

文档：`docs/A_SHARE_INDICATORS.md`。

## STOCK_RS_PULLBACK_v1 信号层

使用完整历史指标和按日期覆盖的股票池，生成回踩后重新站上回踩日高点的次日开盘信号：

```bash
uv run texperiment generate-stock-rs-pullback-signals \
  --indicator-input data/processed/a_share_indicators.parquet \
  --universe-input data/processed/a_share_universe_full.parquet \
  --output data/signals/STOCK_RS_PULLBACK_v1_signals.csv \
  --setup STOCK_RS_PULLBACK_v1 \
  --require-universe
```

`--require-universe` 要求股票池覆盖指标表全部 `date + code`，不能使用单日 `--as-of` 快照。使用 `--include-candidates` 可输出等待、过期和强势失效候选；候选不是正式交易信号。

文档：`docs/STOCK_RS_PULLBACK_SIGNAL_LAYER.md`。

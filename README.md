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
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
texperiment config-check
pytest -q
```

Without installing:

```bash
PYTHONPATH=src python -m texperiment.cli config-check
PYTHONPATH=src pytest -q
```

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

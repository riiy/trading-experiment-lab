# STOCK_RS_PULLBACK_v1 Account Simulation Layer

本层在历史验证报告之后运行，用 30,000 元 Trading Experiment 账户约束重新过滤回测交易。

## 输入

```text
data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv
```

可选输入：

```text
data/reports/STOCK_RS_PULLBACK_v1_metrics.json
```

若该文件存在，CLI 默认要求其中的 `decision` 为：

```text
VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION
```

非 PASS 状态下只能使用 `--force-research` 做开发调试，不能作为正式研究结论。

## 命令

```bash
uv run texperiment account-sim-stock-rs-pullback \
  --trade-input data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv \
  --output data/account_sim/STOCK_RS_PULLBACK_v1_account_sim.csv \
  --summary-output data/account_sim/STOCK_RS_PULLBACK_v1_account_summary.json \
  --report-output data/reports/STOCK_RS_PULLBACK_v1_account_simulation_report.md
```

开发调试可用：

```bash
uv run texperiment account-sim-stock-rs-pullback --force-research
```

正式模式默认要求 `data/reports/STOCK_RS_PULLBACK_v1_metrics.json` 的 `decision` 为 `VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION`。`--force-research` 仅用于开发调试，输出会标记 `force_research=true`，不构成正式账户仿真结论。

## 输出

```text
data/account_sim/STOCK_RS_PULLBACK_v1_account_sim.csv
data/account_sim/STOCK_RS_PULLBACK_v1_account_summary.json
data/reports/STOCK_RS_PULLBACK_v1_account_simulation_report.md
```

## 状态

已接受交易：

```text
accepted_trade
```

拒绝或跳过：

```text
rejected_or_skipped
```

常见原因：

```text
invalid_one_lot_too_expensive
invalid_risk_too_wide
invalid_capital_not_enough
rejected_max_positions
rejected_monthly_loss_limit_reached
rejected_monthly_loss_budget_exceeded
rejected_total_drawdown_budget_exceeded
skipped_after_total_drawdown_freeze
skipped_invalid_backtest_trade
rejected_missing_required_trade_fields
```

## 决策纪律

`ACCOUNT_SIMULATION_PASSED` 只是进入交易票层的前置条件，不是实盘许可。

正式流程仍然是：

```text
历史验证 PASS
↓
账户仿真 PASS
↓
交易票生成
↓
人工复核
↓
Micro Live
```

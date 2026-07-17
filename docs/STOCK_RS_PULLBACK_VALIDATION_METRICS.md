# STOCK_RS_PULLBACK_v1 验证报告指标层

本模块用于把回测交易明细转换为正式验证报告。

它不负责生成交易信号，也不负责账户仿真；它只回答一个问题：

> STOCK_RS_PULLBACK_v1 的回测结果是否达到预注册门槛，是否允许进入 3 万元账户仿真？

## 输入

默认输入：

```bash
uv run texperiment report-stock-rs-pullback \
  --trade-input data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv \
  --metadata-input data/processed/a_share_universe_full.parquet \
  --metrics-output data/reports/STOCK_RS_PULLBACK_v1_metrics.json \
  --report-output data/reports/STOCK_RS_PULLBACK_v1_validation_report.md \
  --yearly-output data/reports/STOCK_RS_PULLBACK_v1_yearly.csv \
  --industry-output data/reports/STOCK_RS_PULLBACK_v1_industry.csv
```

`--metadata-input` 可选，用于补充 `industry` 字段。可以传入带 `date`、`code`、`industry` 的历史 universe，也可以传入静态元数据表。带日期的元数据只使用不晚于每笔交易 `signal_date` 的行业记录，不使用未来标签。空行业值归入 `UNKNOWN`。

## 输出

默认输出：

```text
data/reports/STOCK_RS_PULLBACK_v1_metrics.json
data/reports/STOCK_RS_PULLBACK_v1_validation_report.md
data/reports/STOCK_RS_PULLBACK_v1_yearly.csv
data/reports/STOCK_RS_PULLBACK_v1_industry.csv
```

## 总体指标

核心字段：

```text
rows
valid_trades
invalid_trades
mean_net_return
median_net_return
win_rate
profit_factor
best_3_removed_mean
worst_3_removed_mean
top3_contribution_sum
bottom3_contribution_sum
top3_contribution_ratio
net_return_sum
max_gain
max_loss
mean_r_multiple
median_r_multiple
avg_holding_days
exit_reason_counts
invalid_reason_counts
```

## 通过标准

从 `configs/setups/STOCK_RS_PULLBACK_v1.yaml` 读取：

```yaml
validation_threshold:
  min_valid_trades: 80
  mean_net_return_gt: 0
  median_net_return_gte: 0
  profit_factor_gt: 1.20
  best_3_removed_mean_gte: 0
  top3_contribution_ratio_lte: 1.0
  min_positive_years_or_regimes: 2
```

全部通过才会输出：

```text
VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION
```

否则：

```text
EDGE_NOT_TRADABLE
FAILED_ARCHIVED
```

## Top3 贡献口径

```text
top3_contribution_ratio = Top3 净收益和 / 所有有效交易净收益和
```

若总净收益小于或等于 0，但 Top3 为正，则该比例记为 `inf`，视为不能通过。

该指标用于防止策略收益由极少数极端赢家支撑。

## 年度表现

按 `exit_date` 所属年份聚合：

```text
year
valid_trades
mean_net_return
median_net_return
win_rate
profit_factor
best_3_removed_mean
net_return_sum
```

`min_positive_years_or_regimes` 使用年度平均净收益为正的年份数。

## 行业集中度

按 `industry` 聚合：

```text
industry
valid_trades
trade_share
mean_net_return
median_net_return
win_rate
profit_factor
best_3_removed_mean
net_return_sum
```

若没有行业字段，统一归入 `UNKNOWN`。

## 决策纪律

- `VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION`：只允许进入账户仿真，不等于可实盘。
- `EDGE_NOT_TRADABLE`：不交易，不账户仿真。
- `FAILED_ARCHIVED`：归档，不在同一验证集上修改规则抢救。

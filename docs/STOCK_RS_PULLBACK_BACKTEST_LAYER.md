# STOCK_RS_PULLBACK_v1 回测层

当前层把 `triggered_entry_next_open` 信号转换为可审计的历史交易结果。

## 输入

### 信号文件

默认路径：

```text
data/signals/STOCK_RS_PULLBACK_v1_signals.csv
```

只回测：

```text
status = triggered_entry_next_open
```

必要字段：

```text
signal_id
setup_id
code
name
signal_date
pullback_date
trigger_date
status
stop_price
pullback_low
```

### 日线文件

默认路径：

```text
data/processed/a_share_daily.parquet
```

必要字段：

```text
date
code
open
high
low
close
```

可选但建议存在：

```text
is_suspended
is_limit_up
is_limit_down
```

## 执行规则

### 1. 次日开盘入场

信号触发日为 `trigger_date`，入场日为该股票下一个交易日：

```text
entry_date = next_trading_day_after(trigger_date)
entry_price = entry_day.open
```

若无下一个交易日，输出：

```text
invalid_no_next_open
```

若入场日涨停或停牌，输出：

```text
invalid_limit_up_cannot_buy
invalid_suspended_cannot_buy
```

## 2. 结构止损

```text
stop_price = signal.stop_price
```

通常来自回踩日低点：

```text
stop_price = pullback_low
```

若止损价不低于入场价，输出：

```text
invalid_stop_not_below_entry
```

## 3. 2R目标

```text
R = entry_price - stop_price
target_price = entry_price + 2 * R
```

若价格跳空越过目标位，按开盘价成交；否则按目标价成交。

目标命中：

```text
exit_reason = target_2r
exit_price = target_price
```

## 4. A股 T+1 处理

默认：

```yaml
allow_same_day_exit: false
```

因此，入场日即使触及止损或目标，也不在当日退出。最早退出日为入场后的下一个交易日。

## 5. 止损与目标同日命中

日线无法知道盘中先后顺序，默认保守处理：

```yaml
intraday_priority: stop_first
```

即同日同时触及止损和目标时，按止损退出。

若开盘价已经低于止损价或高于目标价，使用开盘价；否则按 `stop_first` / `target_first` 处理日内触发。

## 6. 5日无上攻退出

默认定义：入场后第5个持有交易日，如果区间最高价仍未达到 `+1R`，则按第5日收盘价退出。

```text
progress_price = entry_price + 1R
if max(high from D1 to D5) < progress_price:
    exit at D5 close
    exit_reason = time_stop_no_upside_progress
```

## 7. 10日退出

若未触发止损、目标或5日无上攻退出，则第10个持有交易日收盘退出：

```text
exit_reason = max_holding_exit
```

## 8. 成本

默认往返成本：

```yaml
round_trip_cost: 0.002
```

收益计算：

```text
gross_return = exit_price / entry_price - 1
net_return = gross_return - round_trip_cost
r_multiple = (exit_price - entry_price) / (entry_price - stop_price)
```

## 输出

默认路径：

```text
data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv
```

输出字段：

```text
trade_id
signal_id
setup_id
code
name
signal_date
pullback_date
trigger_date
entry_date
entry_price
stop_price
target_price
exit_date
exit_price
exit_reason
gross_return
net_return
r_multiple
holding_days
round_trip_cost
status
invalid_reason
```

## CLI

```bash
uv run texperiment backtest-stock-rs-pullback \
  --signal-input data/signals/STOCK_RS_PULLBACK_v1_signals.csv \
  --daily-input data/processed/a_share_daily.parquet \
  --output data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv \
  --setup STOCK_RS_PULLBACK_v1 \
  --batch-size 250000
```

回测只处理 `status=triggered_entry_next_open`，候选审计行不会生成交易记录。
Parquet 日线输入按股票代码分批处理；内存较小时降低 `--batch-size`。

## 当前边界

已完成：

```text
次日开盘入场
结构止损
2R目标
10日退出
5日无上攻退出
A股T+1默认处理
往返成本扣除
无效交易原因标记
```

未完成：

```text
组合层账户仿真
3万元仓位约束
单笔最大计划亏损检查
年度 / 行业 / Top3贡献验证报告
正式交易票生成
```

当前状态仍然是：

```text
trading_allowed = false
```

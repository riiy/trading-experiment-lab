# STOCK_RS_PULLBACK_v1 信号层

本模块负责把已经完成的 A 股指标表转成 `STOCK_RS_PULLBACK_v1` 的可回测信号。

当前仍处于研究阶段：

```text
trading_allowed = false
live_trading = forbidden
```

## 输入

默认输入：

```text
data/processed/a_share_indicators.parquet
data/processed/a_share_universe.parquet
```

指标表至少需要：

```text
date, code, open, high, low, close, volume,
ma20, ma60, excess_ret20,
drawdown_from_10d_high, vol_ma5
```

股票池表至少需要：

```text
date, code, is_tradable_universe
```

使用 `--require-universe` 时，股票池必须覆盖指标表中的每个 `date + code`。用于回测的股票池应从完整日线生成并带 `--include-rejected`，不要使用默认只保留合格股票的输出，也不要使用带 `--as-of` 只包含单个交易日的快照。

可选字段：

```text
name, is_suspended, is_limit_up, is_limit_down,
volume_ratio_to_ma5, breakout_body_midpoint,
avg_amount_20d, one_lot_value, reject_reasons
```

## 规则顺序

信号层严格按以下顺序处理：

```text
强势过滤
↓
首次有序缩量回踩
↓
等待重新站上回踩日高点
↓
生成 next_day_open 入场信号
```

## 强势过滤

默认条件：

```text
excess_ret20 > 0.05
close > ma20
ma20 > ma60
近20日内出现过20日新高
```

其中“近20日内出现过20日新高”的实现方式是：

```text
某交易日 high >= 该日前20个交易日最高价
且该事件发生在当前日前20个交易日窗口内
```

该字段只使用当前和历史数据，不使用未来数据。

## 回踩过滤

默认条件：

```text
0.03 <= drawdown_from_10d_high <= 0.08
volume < vol_ma5
close > ma20
若 breakout_body_midpoint 存在，则 close >= breakout_body_midpoint
```

当前版本允许 `breakout_body_midpoint` 缺失。缺失时不因为该字段拒绝信号；后续版本可以补精确突破阳线实体中点识别。

## 重新站上回踩日高点

在强势状态中发现第一个合格回踩日后，系统等待后续交易日：

```text
trigger_close > pullback_high
```

默认等待窗口：

```text
trigger_window_days = 5
```

如果 5 个交易日内没有重新站上回踩日高点，则该候选信号过期。

## 首次回踩约束

默认：

```yaml
require_first_pullback_in_strength_regime: true
```

含义：同一段强势状态内，只接受第一个合格回踩。若第一个回踩未触发入场，不在同一强势段里继续寻找第二个、第三个回踩，避免事后挑选。

## 输出

默认输出：

```text
data/signals/STOCK_RS_PULLBACK_v1_signals.csv
```

核心字段：

```text
signal_id
setup_id
code
name
signal_date
pullback_date
trigger_date
status
entry_execution
pullback_high
pullback_low
stop_price
trigger_close
days_to_trigger
excess_ret20_at_pullback
drawdown_from_10d_high_at_pullback
volume_ratio_to_ma5_at_pullback
is_tradable_universe_at_pullback
is_tradable_universe_at_trigger
invalid_reason
```

正式可回测信号的状态为：

```text
triggered_entry_next_open
```

使用 `--include-candidates` 时，还会输出审计状态：

```text
candidate_pending_reclaim
candidate_expired_no_reclaim
candidate_expired_strength_lost
```

这些不是正式入场信号。

## CLI

生成正式触发信号：

```bash
uv run texperiment generate-stock-rs-pullback-signals \
  --indicator-input data/processed/a_share_indicators.parquet \
  --universe-input data/processed/a_share_universe_full.parquet \
  --output data/signals/STOCK_RS_PULLBACK_v1_signals.csv \
  --setup STOCK_RS_PULLBACK_v1 \
  --require-universe \
  --batch-size 250000
```

带候选审计：

```bash
uv run texperiment generate-stock-rs-pullback-signals \
  --indicator-input data/processed/a_share_indicators.parquet \
  --universe-input data/processed/a_share_universe_full.parquet \
  --output data/signals/STOCK_RS_PULLBACK_v1_signals_audit.csv \
  --setup STOCK_RS_PULLBACK_v1 \
  --include-candidates \
  --require-universe \
  --allow-empty \
  --batch-size 250000
```

## 当前边界

已完成：

```text
强势过滤
回踩过滤
近20日新高事件识别
同一强势段首个回踩约束
5个交易日内重新站上回踩日高点
输出正式触发信号和候选审计状态
```

未完成：

```text
next_day_open 实际入场价格
结构止损/2R/10日退出回测
账户3万元约束下的仓位仿真
正式交易票生成
```

下一步：

```text
回测层：次日开盘入场 + 结构止损 + 2R目标 + 10日退出 + 5日无上攻退出 + 成本
```

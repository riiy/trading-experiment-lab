# Account Simulation Rules

账户仿真只在历史验证 PASS 后启动。它的任务不是寻找收益，而是判断 `STOCK_RS_PULLBACK_v1` 的回测交易能否被 30,000 元 Trading Experiment 账户真实执行。

## 1. 资金约束

```text
capital_limit = 30,000 CNY
max_planned_loss_per_trade = 500 CNY
max_monthly_loss = 1,500 CNY
max_total_drawdown = 3,000 CNY
max_positions = 1
lot_size = 100
max_one_lot_value = 15,000 CNY
```

## 2. 仓位计算

```text
per_share_risk = entry_price - stop_price
raw_shares = max_planned_loss / per_share_risk
shares = floor(raw_shares / lot_size) * lot_size
capital_used = shares * entry_price
planned_loss = shares * per_share_risk
pnl = capital_used * net_return
```

`net_return` 来自回测层，已经扣除往返成本。

## 3. 单笔风险规则

以下情况拒绝交易：

- `per_share_risk <= 0`；
- `shares < lot_size`；
- `capital_used > 30,000`；
- `planned_loss > 500`；
- 一手金额 `entry_price * 100 > 15,000`；
- 缺少必要价格字段。

## 4. 最大持仓规则

账户最多同时持有 1 只股票。

如果某个信号的 `entry_date` 早于或等于上一笔已接受交易的 `exit_date`，则拒绝：

```text
rejected_max_positions
```

## 5. 月度亏损限制

按入场月份记录已实现盈亏。若当月实际亏损达到 `-1,500`，后续该月信号拒绝；若单笔实际亏损因跳空超过计划亏损，账户仿真仍标记月度限制失败。

在开仓前还会做计划亏损预算检查：

```text
current_month_pnl - planned_loss < -max_monthly_loss
```

若触发，则拒绝：

```text
rejected_monthly_loss_budget_exceeded
```

## 6. 总回撤冻结线

若账户累计实现亏损达到 `-3,000`，账户冻结，后续信号跳过：

```text
skipped_after_total_drawdown_freeze
```

开仓前也会检查：

```text
cumulative_pnl - planned_loss < -max_total_drawdown
```

若触发，则拒绝：

```text
rejected_total_drawdown_budget_exceeded
```

## 7. 输出字段

账户仿真输出：

```text
simulation_id
trade_id
setup_id
code
name
entry_date
exit_date
entry_price
stop_price
target_price
exit_price
exit_reason
net_return
r_multiple
shares
capital_used
per_share_risk
planned_loss
pnl
cumulative_pnl
account_equity
peak_equity
drawdown_from_peak
monthly_realized_pnl
consecutive_losses
status
invalid_reason
```

## 8. 决策口径

```text
ACCOUNT_SIMULATION_PASSED
ACCOUNT_SIMULATION_FAILED
```

通过账户仿真不等于可以自动交易。它只允许进入下一层：交易票生成与人工复核。

失败时：

- 不生成正式交易票；
- 不进入 Micro Live；
- 不扩大 30,000 元额度；
- 不允许通过降低风险规则抢救。

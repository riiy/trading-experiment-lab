# Account Simulation Rules

账户仿真只在历史验证 PASS 后启动。

## 资金约束

```text
capital_limit = 30,000 CNY
max_planned_loss_per_trade = 500 CNY
micro_live_initial_loss_limit = 300 CNY
max_positions = 1
```

## 仓位计算

```text
per_share_risk = entry_price - stop_price
raw_shares = max_planned_loss / per_share_risk
shares = floor(raw_shares / lot_size) * lot_size
capital_used = shares * entry_price
planned_loss = shares * per_share_risk
```

## 无效信号

以下情况信号无效：

- per_share_risk <= 0；
- shares < lot_size；
- capital_used > 30,000；
- planned_loss > 500；
- 一手金额 > 15,000；
- 次日开盘涨停无法买入；
- 缺少必要价格数据。

## 暂停规则

- 连续 1 次规则违规：暂停；
- 连续 3 笔亏损：暂停复盘；
- 月度亏损达到 1,500 元：当月停止；
- 总亏损达到 3,000 元：冻结实验账户。

# STOCK_RS_PULLBACK_v1 交易票生成层

## 1. 目标

交易票生成层负责把账户仿真中的 `accepted_trade` 转换为人工复核用 Markdown 交易票。

它不负责下单，也不允许包含任何自动下单能力。

```text
account_sim accepted_trade
↓
risk field validation
↓
markdown trade ticket
↓
manual review only
```

## 2. 输入

默认输入：

```bash
data/account_sim/STOCK_RS_PULLBACK_v1_account_sim.csv
data/account_sim/STOCK_RS_PULLBACK_v1_account_summary.json
```

只有账户仿真结论为：

```text
ACCOUNT_SIMULATION_PASSED
```

才允许正式生成交易票。开发调试时可以使用 `--force-research`，但不能用于实盘决策。

## 3. 校验字段

每张票必须包含：

```text
setup_id
code
entry_date
entry_price
stop_price
target_price
shares
planned_loss
capital_used
per_share_risk
```

并且必须满足：

```text
status == accepted_trade
stop_price < entry_price
target_price > entry_price
shares > 0
shares 是 100 股整数倍
planned_loss <= 500
capital_used <= 30000
entry_price * 100 <= 15000
per_share_risk == entry_price - stop_price
planned_loss == per_share_risk * shares
capital_used == entry_price * shares
```

## 4. 禁止自动下单

校验器会拒绝以下字段：

```text
broker
broker_account
order_id
order_type
submit_order
auto_submit
live_order
api_key
```

生成的交易票只允许作为人工复核材料。

## 5. CLI

```bash
uv run texperiment generate-stock-rs-pullback-tickets \
  --account-sim-input data/account_sim/STOCK_RS_PULLBACK_v1_account_sim.csv \
  --summary-input data/account_sim/STOCK_RS_PULLBACK_v1_account_summary.json \
  --output-dir data/tickets/draft \
  --index-output data/tickets/STOCK_RS_PULLBACK_v1_ticket_index.csv \
  --summary-output data/tickets/STOCK_RS_PULLBACK_v1_ticket_summary.json \
  --report-output data/reports/STOCK_RS_PULLBACK_v1_ticket_generation_report.md
```

筛选单笔：

```bash
uv run texperiment generate-stock-rs-pullback-tickets --trade-id TRADE_ID
uv run texperiment generate-stock-rs-pullback-tickets --simulation-id SIMULATION_ID
```

## 6. 输出

```text
data/tickets/draft/*.md
data/tickets/STOCK_RS_PULLBACK_v1_ticket_index.csv
data/tickets/STOCK_RS_PULLBACK_v1_ticket_summary.json
data/reports/STOCK_RS_PULLBACK_v1_ticket_generation_report.md
```

## 7. 决策状态

```text
TICKET_GENERATION_READY_FOR_MANUAL_REVIEW
TICKET_GENERATION_REVIEW_REQUIRED
TICKET_GENERATION_FAILED
```

即使输出 `READY_FOR_MANUAL_REVIEW`，也不代表可以自动交易。

```text
交易票 = 人工复核文件
交易票 ≠ 自动下单指令
```

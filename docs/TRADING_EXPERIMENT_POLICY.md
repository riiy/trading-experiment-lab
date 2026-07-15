# Trading Experiment Policy

## 定位

Trading Experiment 是 30,000 元股票实验账户，用于验证股票交易 Setup 是否存在可重复优势。

## 账户边界

- 不影响家庭 ETF 核心资产配置；
- 不承担家庭资产增长主任务；
- 不因长期空仓而降低准入标准。

## 当前状态

```yaml
status: research_active_no_trade
trading_allowed: false
current_setup: STOCK_RS_PULLBACK_v1
```

## 风险红线

- 本金上限：30,000 元；
- 单笔最大计划亏损：500 元；
- Micro Live 前 10 笔：单笔计划亏损 ≤300 元；
- 月度最大亏损：1,500 元；
- 总回撤暂停线：3,000 元；
- 最大同时持仓：1 只。

## 禁止动作

- 加仓摊平；
- 盘中主观临时交易；
- 未验证 Setup 交易；
- 事件驱动失败路径复活；
- 扩大实验账户本金；
- 使用家庭核心资产资金。

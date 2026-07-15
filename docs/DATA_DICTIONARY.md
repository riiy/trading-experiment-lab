# Data Dictionary

## daily bars

| 字段 | 类型 | 说明 |
|---|---|---|
| date | date | 交易日期 |
| code | string | 股票代码 |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | float | 成交量 |
| amount | float | 成交额 |
| adj_factor | float | 复权因子，可选 |
| is_suspended | bool | 是否停牌 |
| is_limit_up | bool | 是否涨停 |
| is_limit_down | bool | 是否跌停 |
| is_st | bool | 是否 ST / *ST |
| listing_days | int | 上市天数 |
| industry | string | 行业分类，可选 |

## indicators

| 字段 | 说明 |
|---|---|
| ma20 | 20 日均线 |
| ma60 | 60 日均线 |
| ret20 | 个股 20 日收益 |
| benchmark_ret20 | 沪深300 20 日收益 |
| excess_ret20 | 个股相对沪深300 20 日超额收益 |
| high_10d | 近 10 日高点 |
| drawdown_from_10d_high | 从近 10 日高点回撤幅度 |
| vol_ma5 | 5 日平均成交量 |
| breakout_body_midpoint | 突破阳线实体中点 |

## trades

| 字段 | 说明 |
|---|---|
| signal_id | 信号ID |
| code | 股票代码 |
| signal_date | 信号日 |
| entry_date | 入场日 |
| entry_price | 入场价 |
| stop_price | 止损价 |
| target_price | 目标价 |
| exit_date | 退出日 |
| exit_price | 退出价 |
| exit_reason | 退出原因 |
| gross_return | 毛收益 |
| net_return | 扣成本后收益 |
| r_multiple | R 倍数 |
| status | valid_trade / invalid_* |
| invalid_reason | 无效原因 |

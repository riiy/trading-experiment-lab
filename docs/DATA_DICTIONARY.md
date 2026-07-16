# Data Dictionary

## daily bars

| 字段 | 类型 | 说明 |
|---|---|---|
| date | datetime | 交易日期 |
| code | string | 标准股票代码，如 `000001.SZ`、`600000.SH` |
| raw_code | string | 原始数据源代码 |
| name | string | 股票名称，可空 |
| market | string | SH / SZ / BJ |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| pre_close | float | 前收盘价，可空 |
| volume | float | 成交量，统一为股 |
| amount | float | 成交额，统一为人民币元 |
| turnover_rate | float | 换手率，可空 |
| pct_chg | float | 涨跌幅，可空 |
| adj_type | string | none / qfq / hfq |
| adj_factor | float | 复权因子，可空 |
| trade_status | string | 原始交易状态，可空 |
| is_suspended | bool | 是否停牌 |
| is_limit_up | bool | 是否涨停，当前为近似推导 |
| is_limit_down | bool | 是否跌停，当前为近似推导 |
| is_st | bool | 是否 ST / *ST |
| listing_days | int | 上市天数，可后续补充 |
| industry | string | 行业分类，可选 |
| source | string | 数据来源 |
| source_file | string | 原始文件路径 |

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

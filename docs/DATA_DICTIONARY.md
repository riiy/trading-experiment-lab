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
| listing_days | int | 上市天数；缺失时股票池按每只股票已观察行数回退 |
| industry | string | 行业分类，可选 |
| source | string | 数据来源 |
| source_file | string | 原始文件路径 |

## indicators

由 `compute-a-share-indicators` 生成。

| 字段 | 说明 |
|---|---|
| ma20 | 20 日均线 |
| ma60 | 60 日均线 |
| ret20 | 个股 20 日收益 |
| benchmark_close | 沪深300当日收盘价 |
| benchmark_ret20 | 沪深300 20 日收益 |
| excess_ret20 | 个股相对沪深300 20 日超额收益 |
| relative_strength_20d | 相对沪深300强度，当前等同于 `excess_ret20` |
| high_10d | 近 10 日高点，包含当前交易日 |
| drawdown_from_10d_high | 从近 10 日高点回撤幅度，`1 - close / high_10d` |
| vol_ma5 | 5 日平均成交量 |
| volume_ratio_to_ma5 | 当日成交量 / 5 日平均成交量 |
| close_above_ma20 | 收盘价是否高于MA20 |
| ma20_above_ma60 | MA20是否高于MA60 |
| volume_below_ma5 | 当日成交量是否低于5日均量 |
| has_complete_indicator_window | 核心指标窗口是否完整 |
| benchmark_code | 使用的基准指数代码 |

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


## A股股票池字段

由 `build-a-share-universe` 生成。

| 字段 | 类型 | 说明 |
|---|---|---|
| `avg_amount_20d` | float | 近20日平均成交额，单位人民币元 |
| `one_lot_value` | float | 一手金额，`close * lot_size` |
| `pass_non_st` | bool | 是否通过非ST过滤 |
| `pass_listing_days` | bool | 是否通过上市天数过滤 |
| `pass_not_suspended` | bool | 是否通过停牌/无交易过滤 |
| `pass_not_limit_up_down` | bool | 是否通过涨跌停过滤 |
| `pass_avg_amount_20d` | bool | 是否通过成交额过滤 |
| `pass_one_lot_value` | bool | 是否通过一手金额过滤 |
| `is_tradable_universe` | bool | 是否进入可交易股票池 |
| `reject_reasons` | string | 未通过原因，分号分隔 |

股票池输出同时保留对应的最新标准日线字段。未使用 `--include-rejected` 时，输出只包含 `is_tradable_universe=true` 的行；使用该选项时，同时保留拒绝行及其 `reject_reasons`。

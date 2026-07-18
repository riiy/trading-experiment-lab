# Data Dictionary

## daily bars

| 字段 | 类型 | 说明 |
|---|---|---|
| date | datetime | 交易日期 |
| code | string | 标准股票代码，如 `000001.SZ`、`600000.SH` |
| raw_code | string | 原始数据源代码 |
| name | string | 股票名称，可空 |
| market | string | SH / SZ / BJ |
| board | string | `MAIN_SH` / `MAIN_SZ` / `CHINEXT` / `STAR` / `BEIJING` / `UNKNOWN_BOARD` |
| listing_date | date | 可靠上市日期；缺失时上市阶段规则为 UNKNOWN |
| listing_trading_day | int | 上市后第几个交易日；注册制前5日等特殊阶段必须提供 |
| historical_st_status | string | 时点 ST 状态：TRUE / FALSE / UNKNOWN |
| opening_auction_fill_status / closing_auction_fill_status | string | 独立开盘/收盘集合竞价成交证据：TRUE / FALSE / UNKNOWN |
| open / high / low / close | float | 兼容字段；新代码不得假定其价格口径 |
| pre_close | float | 前收盘价，可空 |
| volume | float | 成交量，统一为股 |
| amount | float | 成交额，统一为人民币元 |
| turnover_rate | float | 换手率，可空 |
| pct_chg | float | 涨跌幅，可空 |
| adj_type | string | none / qfq / hfq |
| adj_factor | float | 仿射复权映射乘数 |
| adj_offset | float | 仿射复权映射偏移，约定 `adj_price = raw_price * adj_factor + adj_offset` |
| adjustment_status | string | `KNOWN_AFFINE_TDX_QFQ_HFQ_VALIDATED` 或具体 UNKNOWN 状态 |
| adjustment_fit_error / hfq_fit_error | float | 前/后复权四价仿射拟合最大绝对误差 |
| raw_open / raw_high / raw_low / raw_close | float | 未复权真实交易价格；成交、涨跌停和跳空执行使用 |
| raw_pre_close | float | 未复权前收盘；法定涨跌停价计算使用 |
| adj_open / adj_high / adj_low / adj_close | float | 连续复权价格；指标和策略结构使用 |
| hfq_open / hfq_high / hfq_low / hfq_close | float | TDX 后复权验证层，不直接用于策略或执行 |
| limit_up_price / limit_down_price | float | 按板块、日期、ST、上市阶段计算的未复权限价 |
| open_at_limit_up/down | string | 开盘是否处于限价，TRUE / FALSE / UNKNOWN |
| close_at_limit_up/down | string | 收盘是否处于限价，TRUE / FALSE / UNKNOWN |
| one_price_limit_up/down | string | 全天 OHLC 是否均锁在限价，TRUE / FALSE / UNKNOWN |
| can_buy_at_open / can_sell_at_open | string | 开盘成交能力，TRUE / FALSE / UNKNOWN |
| can_sell_intraday / can_sell_at_close | string | 盘中和收盘成交能力，TRUE / FALSE / UNKNOWN |
| scheduled_close_fill_status | string | `FILLED_AT_CLOSE` / `ASSUMED_UNFILLED_CONSERVATIVE` / `UNKNOWN` |
| historical_st_branch_status | string | `PASS_BRANCH_INVARIANT` 或 ST 分支差异状态 |
| limit_rule_status | string | `KNOWN_LIMIT`、`KNOWN_NO_DAILY_LIMIT` 或具体 `UNKNOWN_*` |
| limit_rule_reason | string | 规则状态原因 |
| trade_status | string | 原始交易状态，可空 |
| is_suspended | bool | 是否停牌 |
| is_limit_up | bool | 旧兼容字段，仅表示已知收盘涨停；禁止用于开盘成交判断 |
| is_limit_down | bool | 旧兼容字段，仅表示已知收盘跌停；禁止用于开盘成交判断 |
| is_st | bool | 是否 ST / *ST |
| listing_days | int | 上市天数；缺失时股票池按每只股票已观察行数回退 |
| industry | string | 行业分类，可选 |
| source | string | 数据来源 |
| source_file | string | 原始文件路径 |

当 `raw_open` 恰好等于涨停价或跌停价时，后续盘中打开只能证明当日存在交易，不能证明策略在开盘集合竞价成交。缺少独立集合竞价证据时，开盘成交能力保持 `UNKNOWN`。

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
| entry_adjusted_price | 入场价映射到连续复权价格层后的值 |
| entry_adj_factor | 入场日复权因子 |
| stop_price | 止损价 |
| stop_adjusted_price | 策略结构使用的复权止损价 |
| target_price | 目标价 |
| target_adjusted_price | 策略结构使用的复权目标价 |
| exit_date | 退出日 |
| exit_price | 退出价 |
| exit_adjusted_price | 退出价映射到连续复权价格层后的值 |
| exit_adj_factor | 退出日复权因子 |
| exit_reason | 退出原因 |
| gross_return | 毛收益 |
| net_return | 扣成本后收益 |
| r_multiple | R 倍数 |
| status | valid_trade / invalid_* |
| invalid_reason | 无效原因 |

## signal layer

由 `generate-stock-rs-pullback-signals` 生成。

| 字段 | 说明 |
|---|---|
| signal_id | 稳定信号标识 |
| setup_id | Setup 标识 |
| code | 股票代码 |
| signal_date | 信号日；正式信号为触发日 |
| pullback_date | 回踩日 |
| trigger_date | 重新站上回踩日高点日期 |
| status | `triggered_entry_next_open` 或候选审计状态 |
| entry_execution | 当前为 `next_day_open` |
| pullback_high | 回踩日最高价 |
| pullback_low | 回踩日最低价，同时作为结构止损参考价 |
| stop_price | 当前结构止损参考价 |
| trigger_close | 触发日收盘价 |
| days_to_trigger | 回踩日至触发日交易日距离 |
| invalid_reason | 候选过期或强势失效原因 |


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

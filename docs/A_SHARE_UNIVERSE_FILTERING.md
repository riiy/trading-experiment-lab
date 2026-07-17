# A股股票池过滤

本模块用于把标准化后的 A 股日线数据转换为 `STOCK_RS_PULLBACK_v1` 可执行股票池。

当前目标不是选出买入信号，而是先排除 3 万元 Trading Experiment 账户无法执行或风险不合适的股票。

## 输入

默认输入：

```bash
data/processed/a_share_daily.parquet
```

该文件由 `ingest-a-share-daily` 生成，至少需要字段：

| 字段 | 说明 |
|---|---|
| `date` | 交易日期 |
| `code` | 标准证券代码，例如 `000001.SZ` |
| `close` | 收盘价，建议前复权 |
| `amount` | 成交额，单位：人民币元 |

可选字段：

| 字段 | 用途 |
|---|---|
| `name` | 用于辅助识别 ST / *ST / 退市风险 |
| `listing_days` | 上市天数；缺失时用该股票已观察到的交易日数量保守估算 |
| `avg_amount_20d` | 近20日平均成交额；缺失时用 `amount` 滚动计算 |
| `is_st` | ST 标记 |
| `is_suspended` | 停牌标记 |
| `is_limit_up` | 涨停标记 |
| `is_limit_down` | 跌停标记 |
| `trade_status` | 交易状态，用于辅助识别停牌 |
| `volume` | 成交量；若成交量和成交额同时为0，辅助判为停牌/无交易 |
| `pre_close` / `pct_chg` | 缺失涨跌停标记时，用于近似推断涨跌停 |

## 过滤规则

默认读取：

```bash
configs/setups/STOCK_RS_PULLBACK_v1.yaml
```

核心规则：

| 规则 | 默认值 | 目的 |
|---|---:|---|
| 非 ST / 非 *ST | 是 | 排除高风险状态股 |
| 上市满 180 天 | 是 | 排除新股交易状态不稳定期 |
| 非停牌 / 非无交易 | 是 | 保证可买卖 |
| 非涨停 / 非跌停 | 是 | 避免买不到或风险不可控 |
| 近20日平均成交额 | ≥3亿元 | 保证流动性 |
| 一手金额 | ≤15,000元 | 保证 3 万元账户可执行 |

## 命令

生成某个交易日的可交易股票池：

```bash
uv run texperiment build-a-share-universe \
  --input data/processed/a_share_daily.parquet \
  --output data/processed/a_share_universe.parquet \
  --setup STOCK_RS_PULLBACK_v1 \
  --as-of 2026-07-15
```

输出包含通过过滤的股票。

Parquet 输入使用分批处理，默认每批 `250000` 行。内存较小时可降低批大小：

```bash
uv run texperiment build-a-share-universe \
  --input data/processed/a_share_daily.parquet \
  --output data/processed/a_share_universe_full.parquet \
  --setup STOCK_RS_PULLBACK_v1 \
  --include-rejected \
  --batch-size 50000
```

排查为什么股票被过滤：

```bash
uv run texperiment build-a-share-universe \
  --input data/processed/a_share_daily.parquet \
  --output data/processed/a_share_universe_debug.parquet \
  --setup STOCK_RS_PULLBACK_v1 \
  --as-of 2026-07-15 \
  --include-rejected
```

`--include-rejected` 会保留未通过股票，并写入：

| 字段 | 说明 |
|---|---|
| `is_tradable_universe` | 是否进入可交易股票池 |
| `reject_reasons` | 未通过原因，分号分隔 |
| `avg_amount_20d` | 近20日平均成交额 |
| `one_lot_value` | 一手金额 |

## reject_reasons 口径

| 原因 | 含义 |
|---|---|
| `st_or_star_st` | ST、*ST 或退市风险名称 |
| `listing_days_lt_min` | 上市天数不足 |
| `suspended_or_no_trade` | 停牌或无成交 |
| `limit_up_or_limit_down` | 涨停或跌停 |
| `avg_amount_20d_below_min` | 近20日平均成交额低于门槛 |
| `one_lot_value_above_max` | 一手金额超过门槛 |

## 当前边界

已完成：

```text
非ST过滤
上市满180天过滤
近20日成交额过滤
一手金额过滤
停牌/无交易过滤
涨跌停过滤
可解释拒绝原因
CLI输出 parquet
```

仍需后续增强：

```text
精确历史ST状态表
精确上市日期表
按主板/创业板/科创板/北交所区分涨跌停制度
行业分类接入
风险事件表接入
```

当前实现足够支持第一版 `STOCK_RS_PULLBACK_v1` 的股票池预筛选，但后续正式验证前仍应补强历史 ST 和涨跌停精确口径。

# A股指标层

本模块为 `STOCK_RS_PULLBACK_v1` 生成价格结构和相对强度指标。

当前指标层只做事实计算，不做交易判断，不生成买入信号。

## 输入

股票日线：

```text
data/processed/a_share_daily.parquet
```

沪深300指数日线：

```text
data/processed/index_daily.parquet
```

指数数据需要至少包含：

```text
date, code, close
```

默认基准代码：

```text
000300.SH
```

如果你的数据源使用 `399300.SZ` 或其他代码，需要通过 `--benchmark-code` 显式覆盖。

`--daily-input` 必须是完整历史日线数据，至少包含每只股票的 `date`、`code`、`high`、`close`、`volume`。股票池文件通常每只股票只有一个最新快照，不能作为指标输入，否则 MA60、20日收益等窗口无法形成。

## 命令

```bash
uv run texperiment compute-a-share-indicators \
  --daily-input data/processed/a_share_daily.parquet \
  --benchmark-input data/processed/index_daily.parquet \
  --benchmark-code 000300.SH \
  --output data/processed/a_share_indicators.parquet \
  --setup STOCK_RS_PULLBACK_v1
```

Parquet 输入使用分批计算：每批读取固定行数，并为每只股票保留最长指标窗口历史，再追加写入输出文件。该路径适用于大型日线文件；CSV 输入仍采用全量 DataFrame 计算。

如果沪深300指数行已经混在同一个日线文件里，可以省略 `--benchmark-input`：

```bash
uv run texperiment compute-a-share-indicators \
  --daily-input data/processed/a_share_daily.parquet \
  --output data/processed/a_share_indicators.parquet
```

## 输出字段

| 字段 | 说明 |
|---|---|
| `ma20` | 20日均线 |
| `ma60` | 60日均线 |
| `ret20` | 个股20日收益率 |
| `benchmark_close` | 沪深300当日收盘价 |
| `benchmark_ret20` | 沪深300 20日收益率 |
| `excess_ret20` | 个股20日收益率 - 沪深300 20日收益率 |
| `relative_strength_20d` | 相对沪深300强度，当前等同于 `excess_ret20` |
| `high_10d` | 近10日最高价，包含当前交易日 |
| `drawdown_from_10d_high` | 从近10日高点回撤幅度，正数表示回撤 |
| `vol_ma5` | 5日平均成交量 |
| `volume_ratio_to_ma5` | 当日成交量 / 5日平均成交量 |
| `close_above_ma20` | 收盘价是否高于MA20 |
| `ma20_above_ma60` | MA20是否高于MA60 |
| `volume_below_ma5` | 当日成交量是否低于5日均量 |
| `has_complete_indicator_window` | MA60、ret20、benchmark_ret20、high10、vol_ma5 等窗口是否完整 |
| `benchmark_code` | 使用的基准指数代码 |

输出还保留标准日线输入字段，例如 `date`、`code`、`high`、`close`、`volume`，便于后续信号层直接使用。

## 口径

1. 所有滚动指标只使用当前日及之前数据，不使用未来数据。
2. `high_10d` 是包含当前日的10日滚动最高价。
3. `drawdown_from_10d_high = 1 - close / high_10d`。
4. `ret20 = close / close.shift(20) - 1`。
5. `relative_strength_20d` 暂定为20日超额收益，不做复杂因子化。
6. `vol_ma5` 和 `volume_below_ma5` 使用包含当前日的滚动窗口。
7. `has_complete_indicator_window` 要求 MA20、MA60、ret20、benchmark_ret20、high10 和 vol_ma5 等核心字段全部非空。

## 边界

尚未完成：

```text
突破阳线实体中点识别
20日新高出现日期
行业相对强度
港股通指标层
```

这些放到信号层或后续版本，不在当前指标层内混入。

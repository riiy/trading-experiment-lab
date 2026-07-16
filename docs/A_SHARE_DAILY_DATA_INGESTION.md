# A股日线数据接入与字段标准化

## 目标

把不同来源的 A股日线数据统一成项目内部标准格式：

```text
raw provider export
→ canonical daily bars
→ data/processed/a_share_daily.parquet
```

当前阶段只解决 **日线 OHLCV + 成交额 + 基础交易状态**。不在这一层做策略解释，也不做实盘下单。

## 支持的数据来源格式

当前标准化器支持四类输入：

| provider | 说明 |
|---|---|
| canonical | 已经是项目标准字段 |
| akshare | 常见中文列名：日期、股票代码、开盘、收盘、最高、最低、成交量、成交额等 |
| tushare | 常见字段：ts_code、trade_date、open、high、low、close、vol、amount 等 |
| baostock | 常见字段：date、code、open、high、low、close、preclose、volume、amount、tradestatus 等 |

也可以用：

```bash
texperiment ingest-a-share-daily --provider auto ...
```

让系统自动识别。

## AkShare 全市场拉取

安装可选依赖：

```bash
uv sync --extra dev --extra akshare
```

按股票逐只拉取指定日期区间：

```bash
uv run texperiment fetch-a-share-daily \
  --start-date 20200101 \
  --end-date 20261231 \
  --output data/processed/a_share_daily.parquet \
  --adj-type qfq
```

AkShare 股票列表优先使用交易所列表接口，超时或不可用时回退到 Eastmoney 行情列表；列表和逐股历史请求都会重试。单只股票请求失败不会中断其他股票拉取，命令会输出 `symbols_failed`。正式研究前必须检查失败列表并补拉；质量警告模式只允许临时探索。

## 通达信本地日线

通达信本地 `vipdoc/{sh,sz,bj}/lday/*.day` 文件可以避免逐股票网络请求：

```bash
uv run texperiment ingest-tdx-a-share-daily \
  --input /path/to/T0002/vipdoc \
  --output data/processed/a_share_daily.parquet
```

解析器按通达信 `.day` 格式读取价格、成交额和成交量，成交量转换为股。该格式通常是未复权数据，输出默认 `adj_type=none`；不能直接作为 `STOCK_RS_PULLBACK_v1` 的 qfq 历史研究数据。

导入过程按股票文件流式写入 Parquet，不会把全市场数据一次性汇总到内存。

## 标准输出路径

```text
data/processed/a_share_daily.parquet
```

## 标准字段

| 字段 | 说明 |
|---|---|
| date | 交易日期，datetime |
| code | 标准股票代码，如 `000001.SZ`、`600000.SH`、`833000.BJ` |
| raw_code | 原始代码 |
| name | 股票名称，可空 |
| market | SH / SZ / BJ |
| open/high/low/close | 日线价格 |
| pre_close | 前收盘，可空 |
| volume | 成交量，统一为股 |
| amount | 成交额，统一为人民币元 |
| turnover_rate | 换手率，可空 |
| pct_chg | 涨跌幅，可空 |
| adj_type | none / qfq / hfq |
| adj_factor | 复权因子，可空 |
| trade_status | 原始交易状态，可空 |
| is_suspended | 是否停牌 |
| is_limit_up | 是否涨停，当前为近似推导 |
| is_limit_down | 是否跌停，当前为近似推导 |
| is_st | 是否 ST / *ST |
| listing_days | 上市天数，可后续补充 |
| industry | 行业分类，可后续补充 |
| source | 数据来源 |
| source_file | 原始文件路径 |

## 重要口径

### 1. 价格复权

`STOCK_RS_PULLBACK_v1` 的历史验证默认使用：

```text
adj_type = qfq
```

也就是前复权数据。原因是策略需要比较 MA20、MA60、20日收益、回撤等历史价格结构。

注意：

```text
历史研究价格 ≠ 实盘交易票报价
```

后续生成真实交易票时，必须使用当日未复权实时报价或券商行情报价，不得直接用历史前复权价格下单。

### 2. 成交量与成交额

内部统一：

```text
volume = 股
amount = 人民币元
```

已处理的常见差异：

| provider | volume | amount |
|---|---|---|
| akshare | 常见为手，转为股 | 元 |
| tushare | 手，转为股 | 千元，转为元 |
| baostock | 通常为股 | 元 |

### 3. 涨跌停标记

当前 `is_limit_up/is_limit_down` 是根据 `pre_close` 和 `close` 粗略推导：

```text
close / pre_close - 1 >= 9.8%  → is_limit_up
close / pre_close - 1 <= -9.8% → is_limit_down
```

这不是最终精确版本。后续需要按：

```text
主板 / 创业板 / 科创板 / 北交所 / ST
```

分别处理涨跌幅限制。

## 使用方式

把原始文件放到：

```text
data/raw/market/a_share_daily/
```

然后运行：

```bash
texperiment ingest-a-share-daily \
  --input data/raw/market/a_share_daily \
  --output data/processed/a_share_daily.parquet \
  --provider auto \
  --adj-type qfq
```

检查标准化结果：

```bash
texperiment data-check --path data/processed/a_share_daily.parquet
```

## 数据质量闸门

当前会检查：

```text
1. 必填字段是否存在
2. date + code 是否重复
3. OHLC 是否为空或非正数
4. volume/amount 是否为负数
5. 交易日期范围
6. 股票数量
```

质量检查失败时，默认直接报错。临时探索时可以使用：

```bash
texperiment ingest-a-share-daily ... --allow-quality-warnings
```

但正式验证不允许带质量警告进入。

## 当前边界

已完成：

```text
A股日线原始文件读取
常见 provider 字段映射
代码标准化
成交量/成交额单位统一
基础质量检查
标准 parquet 输出
```

未完成：

```text
上市天数自动计算
ST 历史状态精确表
行业分类接入
精确涨跌停规则
复权因子独立管理
分红送转除权审计
港股通日线接入
```

这些不影响第一步字段标准化，但在正式大样本验证前需要逐步补齐。

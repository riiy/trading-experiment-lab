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
uv run texperiment ingest-a-share-daily --provider auto ...
```

让系统自动识别。

`ingest-a-share-daily` 当前读取 CSV 或 Parquet 文件；TDX `.txt` 导出不是该命令的通用输入格式。

### TDX 文本导出

项目另有 TDX 文本解析器，支持 GB18030 编码、文件名 `SH#000001.txt` / `SZ#000001.txt` / `BJ#830000.txt`，并按代码前缀过滤 A 股文件。文件头包含“前复权”时写入 `adj_type=qfq`，否则写入 `adj_type=none`。TDX 股票文本批量写入函数目前未接入通用 CLI；沪深300指数可通过以下 CLI 命令导入：

```bash
uv run texperiment ingest-tdx-export-index-daily \
  --input data/raw/export/SH#000300.txt \
  --output data/processed/index_daily.parquet \
  --code 000300.SH
```

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
| open/high/low/close | provider 兼容价格；新代码必须选择显式价格层 |
| raw_open/raw_high/raw_low/raw_close | 未复权真实交易价格 |
| adj_open/adj_high/adj_low/adj_close | 前复权连续信号价格 |
| hfq_open/hfq_high/hfq_low/hfq_close | 后复权交叉验证价格 |
| pre_close | 前收盘，可空 |
| volume | 成交量，统一为股 |
| amount | 成交额，统一为人民币元 |
| turnover_rate | 换手率，可空 |
| pct_chg | 涨跌幅，可空 |
| adj_type | none / qfq / hfq |
| adj_factor/adj_offset | 仿射复权映射参数，可空 |
| trade_status | 原始交易状态，可空 |
| is_suspended | 是否停牌 |
| is_limit_up/is_limit_down | 旧兼容收盘状态，禁止用于开盘成交判断 |
| is_st | 是否 ST / *ST |
| listing_days | 上市天数；仅可靠来源可用于正式上市阶段判断 |
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

然后运行（标准 CSV/Parquet 输入）：

```bash
uv run texperiment ingest-a-share-daily \
  --input data/raw/market/a_share_daily \
  --output data/processed/a_share_daily.parquet \
  --provider auto \
  --adj-type qfq
```

检查标准化结果：

```bash
uv run texperiment data-check --path data/processed/a_share_daily.parquet
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
uv run texperiment ingest-a-share-daily ... --allow-quality-warnings
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
TDX 前复权/不复权/后复权三层配对导入
raw/adjusted 双价格层和仿射复权映射验证
```

Engine Remediation 使用独立输出，不覆盖原始验证输入：

```bash
uv run texperiment ingest-tdx-paired-a-share-daily \
  --qfq-input data/raw/tdx_text/qfq \
  --raw-input data/raw/tdx_text/raw \
  --hfq-input data/raw/tdx_text/hfq \
  --output data/processed/a_share_daily_remediation.parquet
```

TDX 前复权价格与未复权价格实测不是纯乘法关系。配对导入按日拟合并验证：

```text
adj_price = raw_price * adj_factor + adj_offset
```

后复权层用于交叉验证。无法在1.1分钱容差内验证的行保留 `UNKNOWN_AFFINE_FIT`，不得用于执行真实性 PASS。

三层源文件日期集必须完全一致。若某日任一价格层包含非正 OHLC，该日不能形成有效双价格行并计入 `dropped_invalid_layer_rows`；导入器先从完整 raw 序列计算 `raw_pre_close` 和原始 `listing_trading_day`，再排除该日，避免截断后错误重算前收和上市序号。

未完成：

```text
权威上市日期和上市阶段表
时点 ST 历史状态表
行业分类接入
开盘集合竞价成交证据
`UNKNOWN_AFFINE_FIT` 行的独立公司行为数据核验
分红送转除权审计
港股通日线接入
```

这些不影响第一步字段标准化，但在正式大样本验证前需要逐步补齐。

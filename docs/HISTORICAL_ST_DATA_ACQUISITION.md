# A 股逐日历史 ST 数据取得

## 正式数据契约

新 setup 在排除 ST 时，必须使用独立的状态表，粒度为目标日线输入的每个 `(date, code)`。`historical_st_status` 只能取 `TRUE` 或 `FALSE`；缺失、当前名称、静态股票列表和事后推断均不可替代该表。

当前正式来源为 Tushare Pro `stock_st`。该接口返回每个交易日的 ST 清单，历史覆盖自 2016-01-01，因而覆盖已定义的开发期与最终验证期。访问需要具备该接口权限的 Tushare 令牌。

没有 API 凭据时，可使用已授权导出的 CSMAR `TRD_Dalyr` 数据。其 `Trdsta` 是逐日交易状态；转换器把 ST、*ST 及其复合 ST 状态映射为 `TRUE`，把文档定义的非 ST 状态映射为 `FALSE`，而非依据名称或涨跌幅猜测。

另一条来源是 Windows QMT/xtquant。`xtdata.download_his_st_data()` 下载全市场历史 ST/PT 事件文件，`get_his_st_data()` 使用同一文件返回 ST、*ST、PT 区间。本仓库保存该原始文件并按 QMT 的区间语义展开，PT 也按风险排除状态写为 `TRUE`。

QMT 路径不一定需要在命令中传 API 密钥，但必须有已登录且具备投研数据权限的 QMT/miniQMT 数据中心。仅安装 PyPI `xtquant` 并以空 token 启动独立数据中心不可用；服务器会拒绝为非 xt 用户模式提供沪深市场权限。独立数据中心模式则需要从迅投用户中心取得接口 token。不得把 token 写入仓库或原始证据。

## 获取与验证

令牌仅通过环境变量提供，不能写入仓库、命令历史或原始数据：

```bash
export TUSHARE_TOKEN='...'

uv run texperiment fetch-tushare-historical-st \
  --daily-input data/processed/formal_inputs/VOLATILITY_CONTRACTION_BREAKOUT_v1_20260803/raw_daily.parquet \
  --raw-output data/raw/reference/historical_st/tushare_stock_st_20160717_20260717.jsonl \
  --start-date 2016-07-17 \
  --end-date 2026-07-17

uv run texperiment build-historical-st-status \
  --daily-input data/processed/formal_inputs/VOLATILITY_CONTRACTION_BREAKOUT_v1_20260803/raw_daily.parquet \
  --raw-input data/raw/reference/historical_st/tushare_stock_st_20160717_20260717.jsonl \
  --output data/processed/reference/historical_st_20160717_20260717.parquet \
  --start-date 2016-07-17 \
  --end-date 2026-07-17
```

拉取会对目标日线出现的每个交易日请求一次 `stock_st` 响应，并保存不可含令牌的 JSONL 原始证据。构建步骤会拒绝响应错误、漏交易日、错日期、非法代码、重复 ST 成员、重复日线主键以及任何非 `TRUE/FALSE` 状态。输出行数与目标日线在样本期的行数严格相同。

原始 ST 清单可能含停牌而没有对应日线记录的证券；这些记录会在构建报告中单独计数，但不会进入目标 `(date, code)` 状态表。

CSMAR 导出可直接转换：

```bash
uv run texperiment build-historical-st-status-from-csmar \
  --daily-input data/processed/formal_inputs/VOLATILITY_CONTRACTION_BREAKOUT_v1_20260803/raw_daily.parquet \
  --csmar-input data/raw/reference/historical_st/TRD_Dalyr.csv \
  --output data/processed/reference/historical_st_20160717_20260717.parquet \
  --start-date 2016-07-17 \
  --end-date 2026-07-17
```

QMT 路径需要先登录并启动 Windows QMT/miniQMT 行情数据中心，再在其 Python 环境中运行：

```bash
python scripts/download_qmt_historical_st.py \
  --output data/raw/reference/historical_st/SH_XXXXXX_2011_86400000.csv
```

随后在本仓库转换：

```bash
uv run texperiment build-historical-st-status-from-qmt \
  --daily-input data/processed/formal_inputs/VOLATILITY_CONTRACTION_BREAKOUT_v1_20260803/raw_daily.parquet \
  --qmt-input data/raw/reference/historical_st/SH_XXXXXX_2011_86400000.csv \
  --output data/processed/reference/historical_st_20160717_20260717.parquet \
  --start-date 2016-07-17 \
  --end-date 2026-07-17
```

## 后续使用

该文件只能作为下一次独立 setup 的正式输入候选。不得修改任何已冻结输入或重新开启已停止的 `VOLATILITY_CONTRACTION_BREAKOUT_v1`。

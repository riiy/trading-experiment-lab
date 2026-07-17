# AUDIT_STOCK_RS_PULLBACK_v1

Decision: **ENGINE_ERROR_FOUND**

该审计不改变 `STOCK_RS_PULLBACK_v1 = FAILED_ARCHIVED`，不触发重算、账户仿真、交易票或新 Setup。

## Frozen Inputs

- Plan: `AUDIT_STOCK_RS_PULLBACK_v1_PLAN_v1`
- Git commit: `5ceed91cc59e00146547060f8bbe0cda045c138c`
- Git dirty: `False`
- Sample count: `50`

## Sample Categories

- `extreme_gain`: 3
- `extreme_loss`: 2
- `invalid_trade`: 5
- `max_holding_exit`: 8
- `stop_loss`: 12
- `target_2r`: 10
- `time_stop_no_upside_progress`: 10

## Reviewed Checks

- `FAIL`: 10
- `NOT_EVALUABLE_EXECUTION_REALISM`: 50
- `NOT_EVALUABLE_LIMIT_PRICE`: 50
- `NOT_EVALUABLE_MISSING_HISTORICAL_ST`: 50
- `PASS`: 660

全部 820 条检查已由 `OpenCode (assistant)` 逐项复核，reviewer、reviewed_at、notes 已填写。

## Engine Error

5 笔 invalid 样本均被记录为 `invalid_limit_up_cannot_buy`，但对应交易日均有正成交量、`open < high` 且不是一字涨停。下一交易日开盘委托本可按开盘价成交，日终涨停状态不能反推开盘不可成交。

| Code | Entry date | Open | High | Close | Volume |
| --- | --- | ---: | ---: | ---: | ---: |
| `600221.SH` | 2010-02-04 | 3.44 | 3.82 | 3.82 | 169,533,124 |
| `601059.SH` | 2023-08-04 | 18.51 | 19.78 | 19.78 | 175,566,023 |
| `600229.SH` | 2015-05-14 | 20.07 | 22.32 | 22.32 | 29,183,515 |
| `300039.SZ` | 2022-02-21 | 6.64 | 7.93 | 7.45 | 158,808,007 |
| `000034.SZ` | 2015-11-10 | 10.92 | 12.32 | 12.32 | 34,412,551 |

根因：

- `src/texperiment/backtest/execution_model.py:17` 和 `src/texperiment/setups/stock_rs_pullback_v1/entry.py:5` 将任何 `is_limit_up` 日线直接视为开盘不可买，未区分一字涨停与盘中/收盘涨停。
- `src/texperiment/data/tdx_export_source.py:82` 使用固定 `pct_chg >= 9.8`，未按板块、日期、ST 和上市阶段应用涨跌停制度；`300039.SZ` 在 2022-02-21 涨幅仅 11.03%，仍被错误标记为涨停。
- 自动重建曾复用同一 `is_limit_up` 语义，因此自动 PASS 不足以发现该错误；人工复核已将5笔交易的 `ENTRY_DAY_EXECUTABLE` 和 `INVALID_REASON_RECONSTRUCTION` 共10条检查改为 `FAIL`。

该错误会错误删除本应成交的信号，使有效交易集合和失败指标不完整。依审计停止规则，不修复引擎、不重跑全量回测、不进入诊断或新 Setup。

## Data Limitations

- 50 条 `EXECUTION_REALISM`：仅有 qfq 日线，缺少未复权执行价格和完整复权因子。
- 50 条 `HISTORICAL_ST_STATUS`：缺少可靠的历史时点 ST 状态。
- 50 条 `LIMIT_PRICE_VERIFICATION`：冻结输入不足以完整重建按板块、日期、上市阶段和 ST 状态变化的精确涨跌停价。

这些限制不覆盖已确认的执行模型错误；最终结论按 critical failure 优先级为 `ENGINE_ERROR_FOUND`。

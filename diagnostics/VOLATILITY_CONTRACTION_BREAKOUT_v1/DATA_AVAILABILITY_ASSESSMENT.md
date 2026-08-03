# VOLATILITY_CONTRACTION_BREAKOUT_v1 数据可用性评估

## 技术结论

当前冻结输入不能支持该 setup 的开发诊断或一次性最终验证。策略并未被判定为收益失败；结论是 `NOT_EVALUABLE_MISSING_HISTORICAL_ST`，因为预注册的逐日 ST 排除硬规则无法在现有输入上评估。

## 输入与检查

| 项目 | 结果 |
|---|---:|
| 冻结 raw/qfq 行数 | 10,249,283 |
| 冻结范围 | 2016-04-20 至 2026-07-17 |
| raw/qfq 主键及成交量一致性 | 通过 |
| raw/qfq 映射不可评估行 | 0 |
| `000300.SH` 价格指数行数 | 5,230 |
| `historical_st_status = UNKNOWN` | 10,249,283 / 10,249,283（100%） |
| 按预注册约束的合格 universe 行数 | 0 |

冻结快照为 `data/processed/formal_inputs/VOLATILITY_CONTRACTION_BREAKOUT_v1_20260803/`；其中 raw、qfq 和 `000300.SH` 的 SHA-256 已记录在只读 `formal_input_manifest.json`。

## 为什么这是阻断项

策略要求排除 ST 股票，并沿用逐日涨跌停与可执行性约束。TDX 价格导出不提供时点 ST 历史；把 `UNKNOWN` 假定为非 ST 会引入未经验证的股票池和价格限制分支。因此系统按 fail-closed 将每行判为不可交易。

这不是对信号、回测、账户 CAGR 或相对基准表现的负面结论：这些层均未运行，最终验证也未被消耗。

## 研究处置

在当前冻结数据下，继续调参、生成信号或交易票都没有研究价值，且不被允许。恢复研究的最小条件是提供覆盖相同 `(date, code)` 粒度、取值仅为 `TRUE`/`FALSE` 的可靠时点 ST 历史数据；该数据必须与 raw/qfq/benchmark 一起重新审计和冻结。不得用当前证券简称、静态 ST 名单或事后标签替代。

研究已按用户指示停止；冻结输入与本报告保留，供未来独立复核。

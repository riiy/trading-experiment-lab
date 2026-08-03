# VOLATILITY_CONTRACTION_BREAKOUT_v1 策略与验证实现

本模块是研究专用实现，执行顺序为：全日期 A 股股票池 → qfq 收缩/突破信号 → raw/qfq 映射回测 → 逐日 raw 收盘账户权益 → `000300.SH` 价格指数比较。详细固定规则与样本声明以预注册为准：[`PRE_REGISTRATION_VOLATILITY_CONTRACTION_BREAKOUT_v1.md`](PRE_REGISTRATION_VOLATILITY_CONTRACTION_BREAKOUT_v1.md)。

## 命令

```bash
uv run texperiment generate-volatility-contraction-breakout-signals \
  --daily-input data/processed/frozen_qfq_raw_pair.parquet \
  --universe-input data/processed/vcb_universe.parquet \
  --output data/signals/VOLATILITY_CONTRACTION_BREAKOUT_v1_signals.parquet

uv run texperiment backtest-volatility-contraction-breakout \
  --signal-input data/signals/VOLATILITY_CONTRACTION_BREAKOUT_v1_signals.parquet \
  --daily-input data/processed/frozen_qfq_raw_pair.parquet \
  --output data/trades/VOLATILITY_CONTRACTION_BREAKOUT_v1_trades.parquet
```

开发期使用 `validate-volatility-contraction-breakout --phase development`，输出只可作为诊断。最终验证使用同一命令的 `--phase final`，必须给出账户日线、`000300.SH` 指数和未存在的输出路径；成功或失败后默认 run record 会阻止第二次最终运行。

最终输出保存逐日 `cash`、`position_market_value`、`equity`、`cumulative_cost`、峰值和回撤。若 raw/qfq 映射、成交状态、持仓日 raw 收盘价或基准起止日缺失，命令失败，不产生通过结论。

该命令不会修改 `trading_allowed`、账户仿真许可或交易票许可。

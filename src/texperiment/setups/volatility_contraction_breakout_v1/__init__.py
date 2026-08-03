"""VOLATILITY_CONTRACTION_BREAKOUT_v1 research-only strategy components."""

from .backtest import run_volatility_contraction_breakout_backtest
from .rules import build_volatility_contraction_breakout_signals

__all__ = [
    "build_volatility_contraction_breakout_signals",
    "run_volatility_contraction_breakout_backtest",
]

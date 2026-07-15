import pandas as pd

from texperiment.indicators.moving_average import moving_average
from texperiment.indicators.structure import body_midpoint, is_within_drawdown


def test_moving_average():
    s = pd.Series([1,2,3,4,5])
    assert moving_average(s, 3).iloc[-1] == 4


def test_structure_helpers():
    assert body_midpoint(10, 14) == 12
    assert is_within_drawdown({"drawdown_from_10d_high": 0.05}) is True
    assert is_within_drawdown({"drawdown_from_10d_high": 0.10}) is False

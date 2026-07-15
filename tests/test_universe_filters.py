import pandas as pd

from texperiment.universe.a_share import filter_a_share_universe


def test_filter_a_share_universe_keeps_executable_stock():
    df = pd.DataFrame([
        {"code":"A", "close":50, "is_st":False, "listing_days":300, "is_suspended":False, "is_limit_up":False, "is_limit_down":False, "avg_amount_20d":400_000_000},
        {"code":"B", "close":200, "is_st":False, "listing_days":300, "is_suspended":False, "is_limit_up":False, "is_limit_down":False, "avg_amount_20d":400_000_000},
        {"code":"C", "close":20, "is_st":True, "listing_days":300, "is_suspended":False, "is_limit_up":False, "is_limit_down":False, "avg_amount_20d":400_000_000},
    ])
    out = filter_a_share_universe(df)
    assert list(out["code"]) == ["A"]

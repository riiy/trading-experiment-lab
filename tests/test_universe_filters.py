import pandas as pd

from texperiment.universe.a_share import filter_a_share_universe


def test_filter_a_share_universe_keeps_executable_stock():
    df = pd.DataFrame([
        {"code":"A", "close":50, "raw_close":50, "historical_st_status":"FALSE", "listing_days":300, "is_suspended":False, "one_price_limit_up":"FALSE", "one_price_limit_down":"FALSE", "avg_amount_20d":400_000_000},
        {"code":"B", "close":200, "raw_close":200, "historical_st_status":"FALSE", "listing_days":300, "is_suspended":False, "one_price_limit_up":"FALSE", "one_price_limit_down":"FALSE", "avg_amount_20d":400_000_000},
        {"code":"C", "close":20, "raw_close":20, "historical_st_status":"TRUE", "listing_days":300, "is_suspended":False, "one_price_limit_up":"FALSE", "one_price_limit_down":"FALSE", "avg_amount_20d":400_000_000},
    ])
    out = filter_a_share_universe(df)
    assert list(out["code"]) == ["A"]

from __future__ import annotations

import pandas as pd

from texperiment.universe.a_share import (
    AShareUniverseConfig,
    annotate_a_share_universe,
    build_a_share_universe,
    build_a_share_universe_from_parquet,
)


def _row(code: str, date: str, close: float, amount: float, **extra):
    base = {
        "code": code,
        "date": date,
        "close": close,
        "amount": amount,
        "volume": 1_000_000,
        "pre_close": close / 1.01,
        "pct_chg": 1.0,
        "is_st": False,
        "is_suspended": False,
        "is_limit_up": False,
        "is_limit_down": False,
    }
    base.update(extra)
    return base


def test_build_universe_derives_20d_amount_and_listing_days():
    rows = []
    dates = pd.date_range("2026-01-01", periods=181, freq="D")
    for d in dates:
        rows.append(_row("000001.SZ", d.strftime("%Y-%m-%d"), 50, 400_000_000))
    df = pd.DataFrame(rows)

    out = build_a_share_universe(df, as_of_date="2026-06-30")

    assert len(out) == 1
    assert out.loc[0, "code"] == "000001.SZ"
    assert out.loc[0, "avg_amount_20d"] == 400_000_000
    assert out.loc[0, "listing_days"] == 181
    assert out.loc[0, "one_lot_value"] == 5_000


def test_universe_rejects_st_new_suspended_limit_low_amount_and_expensive():
    rows = []
    dates = pd.date_range("2026-01-01", periods=181, freq="D")
    for d in dates:
        ds = d.strftime("%Y-%m-%d")
        rows.append(_row("GOOD.SZ", ds, 50, 400_000_000))
        rows.append(_row("STBAD.SZ", ds, 50, 400_000_000, name="*ST测试"))
        rows.append(_row("HALT.SZ", ds, 50, 400_000_000, is_suspended=True))
        rows.append(_row("LIMIT.SZ", ds, 50, 400_000_000, is_limit_up=True))
        rows.append(_row("ILLIQ.SZ", ds, 50, 100_000_000))
        rows.append(_row("PRICEY.SZ", ds, 200, 400_000_000))
    # NEW only has 100 rows, therefore listing_days derived from observed bars is below 180.
    for d in dates[-100:]:
        rows.append(_row("NEW.SZ", d.strftime("%Y-%m-%d"), 50, 400_000_000))

    annotated = annotate_a_share_universe(pd.DataFrame(rows), as_of_date="2026-06-30")
    eligible = annotated.loc[annotated["is_tradable_universe"]]

    assert list(eligible["code"]) == ["GOOD.SZ"]
    reasons = dict(zip(annotated["code"], annotated["reject_reasons"]))
    assert "st_or_star_st" in reasons["STBAD.SZ"]
    assert "listing_days_lt_min" in reasons["NEW.SZ"]
    assert "suspended_or_no_trade" in reasons["HALT.SZ"]
    assert "limit_up_or_limit_down" in reasons["LIMIT.SZ"]
    assert "avg_amount_20d_below_min" in reasons["ILLIQ.SZ"]
    assert "one_lot_value_above_max" in reasons["PRICEY.SZ"]


def test_config_can_relax_thresholds_for_small_fixture():
    df = pd.DataFrame([
        _row("000001.SZ", "2026-01-01", 10, 1_000_000, listing_days=300, avg_amount_20d=1_000_000),
    ])
    cfg = AShareUniverseConfig(min_listing_days=1, min_avg_amount_20d=1, max_one_lot_value=2_000)
    out = build_a_share_universe(df, config=cfg)
    assert len(out) == 1


def test_listing_days_uses_calendar_days_from_first_observation():
    df = pd.DataFrame(
        [
            _row("000001.SZ", "2026-01-01", 10, 400_000_000, avg_amount_20d=400_000_000),
            _row("000001.SZ", "2026-01-02", 10, 400_000_000, avg_amount_20d=400_000_000),
            _row("000001.SZ", "2026-01-10", 10, 400_000_000, avg_amount_20d=400_000_000),
        ]
    )

    out = build_a_share_universe(
        df,
        as_of_date="2026-01-10",
        config=AShareUniverseConfig(min_listing_days=10),
    )

    assert out.loc[0, "listing_days"] == 10


def test_missing_tdx_st_metadata_is_rejected():
    df = pd.DataFrame(
        [_row("000001.SZ", "2026-07-15", 10, 400_000_000, source="tongdaxin", name="", avg_amount_20d=400_000_000)]
    )

    out = annotate_a_share_universe(
        df,
        config=AShareUniverseConfig(min_listing_days=1, min_avg_amount_20d=1),
    )

    assert bool(out.loc[0, "st_metadata_available"]) is False
    assert "missing_st_metadata" in out.loc[0, "reject_reasons"]


def test_board_specific_limit_rates_are_applied():
    rows = [
        _row("000001.SZ", "2026-07-15", 10, 400_000_000, pct_chg=9.9, pre_close=10 / 1.099, name="普通股"),
        _row("300001.SZ", "2026-07-15", 10, 400_000_000, pct_chg=19.9, pre_close=10 / 1.199, name="创业板股"),
        _row("688001.SH", "2026-07-15", 10, 400_000_000, pct_chg=19.9, pre_close=10 / 1.199, name="科创板股"),
        _row("920001.BJ", "2026-07-15", 10, 400_000_000, pct_chg=29.9, pre_close=10 / 1.299, name="北交所股"),
    ]

    out = annotate_a_share_universe(
        pd.DataFrame(rows),
        config=AShareUniverseConfig(min_listing_days=1, min_avg_amount_20d=1),
    )

    assert out["pass_not_limit_up_down"].tolist() == [False, False, False, False]
    assert out["limit_rate"].tolist() == [0.1, 0.2, 0.2, 0.3]


def test_non_trading_as_of_uses_latest_available_date():
    df = pd.DataFrame(
        [
            _row("000001.SZ", "2026-07-17", 10, 400_000_000),
            _row("000001.SZ", "2026-07-20", 10, 400_000_000),
        ]
    )

    out = annotate_a_share_universe(
        df,
        as_of_date="2026-07-18",
        config=AShareUniverseConfig(min_listing_days=1, min_avg_amount_20d=1),
    )

    assert out.loc[0, "date"] == pd.Timestamp("2026-07-17")
    assert out.loc[0, "effective_as_of"] == pd.Timestamp("2026-07-17")


def test_build_universe_from_parquet_reads_batches(tmp_path):
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    df = pd.DataFrame([_row("000001.SZ", d.strftime("%Y-%m-%d"), 10, 400_000_000) for d in dates])
    path = tmp_path / "bars.parquet"
    df.to_parquet(path, index=False)

    out = build_a_share_universe_from_parquet(
        path,
        as_of_date="2026-01-20",
        config=AShareUniverseConfig(min_listing_days=20),
        batch_size=7,
    )

    assert len(out) == 1
    assert out.loc[0, "avg_amount_20d"] == 400_000_000
    assert out.loc[0, "listing_days"] == 20

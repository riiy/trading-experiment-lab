from __future__ import annotations

import pandas as pd

from texperiment.universe.a_share import (
    AShareUniverseConfig,
    annotate_a_share_universe,
    build_a_share_universe,
    write_a_share_universe_from_parquet,
)


def _row(code: str, date: str, close: float, amount: float, **extra):
    base = {
        "code": code,
        "date": date,
        "close": close,
        "raw_close": close,
        "listing_date": "2020-01-01",
        "amount": amount,
        "volume": 1_000_000,
        "pre_close": close / 1.01,
        "pct_chg": 1.0,
        "is_st": False,
        "historical_st_status": "FALSE",
        "is_suspended": False,
        "is_limit_up": False,
        "is_limit_down": False,
        "one_price_limit_up": "FALSE",
        "one_price_limit_down": "FALSE",
    }
    base.update(extra)
    return base


def test_build_universe_derives_20d_amount_and_listing_days():
    rows = []
    dates = pd.date_range("2026-01-01", periods=181, freq="D")
    for d in dates:
        rows.append(_row("000001.SZ", d.strftime("%Y-%m-%d"), 50, 400_000_000, listing_date="2026-01-01"))
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
        rows.append(_row("STBAD.SZ", ds, 50, 400_000_000, name="*ST测试", historical_st_status="TRUE"))
        rows.append(_row("HALT.SZ", ds, 50, 400_000_000, is_suspended=True))
        rows.append(_row("LIMIT.SZ", ds, 50, 400_000_000, is_limit_up=True, one_price_limit_up="TRUE"))
        rows.append(_row("ILLIQ.SZ", ds, 50, 100_000_000))
        rows.append(_row("PRICEY.SZ", ds, 200, 400_000_000))
    # NEW only has 100 rows, therefore listing_days derived from observed bars is below 180.
    new_listing_date = dates[-100].strftime("%Y-%m-%d")
    for d in dates[-100:]:
        rows.append(_row("NEW.SZ", d.strftime("%Y-%m-%d"), 50, 400_000_000, listing_date=new_listing_date))

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


def test_unknown_historical_status_and_limit_rule_fail_closed():
    row = _row(
        "000001.SZ",
        "2026-01-01",
        10,
        1_000_000,
        listing_days=300,
        avg_amount_20d=1_000_000,
        historical_st_status="UNKNOWN",
        one_price_limit_up="UNKNOWN",
        one_price_limit_down="UNKNOWN",
    )
    cfg = AShareUniverseConfig(min_listing_days=1, min_avg_amount_20d=1, max_one_lot_value=2_000)

    out = annotate_a_share_universe(pd.DataFrame([row]), config=cfg)

    assert bool(out.loc[0, "is_tradable_universe"]) is False
    assert "st_or_star_st" in out.loc[0, "reject_reasons"]
    assert "limit_up_or_limit_down" in out.loc[0, "reject_reasons"]


def test_streaming_universe_matches_full_history_calculation(tmp_path):
    rows = []
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    for d in dates:
        rows.append(_row("000001.SZ", d.strftime("%Y-%m-%d"), 10, 1_000_000))
    source = pd.DataFrame(rows).sort_values(["code", "date"])
    config = AShareUniverseConfig(min_listing_days=1, min_avg_amount_20d=1, max_one_lot_value=2_000)
    expected = annotate_a_share_universe(source, config=config)
    daily_path = tmp_path / "daily.parquet"
    output_path = tmp_path / "universe.parquet"
    source.to_parquet(daily_path, index=False)

    rows_written, eligible_count = write_a_share_universe_from_parquet(
        daily_path,
        output_path,
        config=config,
        include_rejected=True,
        batch_size=7,
    )
    actual = pd.read_parquet(output_path).sort_values(["code", "date"]).reset_index(drop=True)
    expected = expected.sort_values(["code", "date"]).reset_index(drop=True)

    assert rows_written == len(expected)
    assert eligible_count == int(expected["is_tradable_universe"].sum())
    pd.testing.assert_frame_equal(
        actual[["date", "code", "listing_days", "avg_amount_20d", "is_tradable_universe"]],
        expected[["date", "code", "listing_days", "avg_amount_20d", "is_tradable_universe"]],
        check_dtype=False,
    )

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class AShareIndicatorConfig:
    """Indicator parameters required by STOCK_RS_PULLBACK_v1."""

    ma_short_window: int = 20
    ma_long_window: int = 60
    return_window: int = 20
    benchmark_code: str = "000300.SH"
    high_lookback_window: int = 10
    volume_ma_window: int = 5

    @classmethod
    def from_setup_config(cls, setup_config: dict[str, Any]) -> "AShareIndicatorConfig":
        strength = setup_config.get("strength_filter", {})
        pullback = setup_config.get("pullback_filter", {})
        return cls(
            ma_short_window=int(strength.get("ma_short_window", 20)),
            ma_long_window=int(strength.get("ma_long_window", 60)),
            return_window=int(strength.get("lookback_days", 20)),
            benchmark_code=str(strength.get("benchmark", "000300.SH")),
            high_lookback_window=int(pullback.get("high_lookback_days", 10)),
            volume_ma_window=int(pullback.get("volume_ma_window", 5)),
        )


def build_a_share_indicators(
    daily_bars: pd.DataFrame,
    *,
    benchmark_bars: pd.DataFrame | None = None,
    config: AShareIndicatorConfig | None = None,
) -> pd.DataFrame:
    """Build A-share indicator rows for rule-based setup validation.

    The function uses only current and historical rows for each stock. It does not
    use future prices. Rolling windows are inclusive of the current trading date,
    which matches a close-of-day signal generation workflow.

    Required stock columns: ``date``, ``code``, ``close``, ``high``, ``volume``.
    Required benchmark columns: ``date``, ``code``, ``close``. If ``benchmark_bars``
    is omitted, ``daily_bars`` is searched for ``config.benchmark_code``.
    """
    cfg = config or AShareIndicatorConfig()
    stock = _prepare_stock_bars(daily_bars)
    benchmark = _prepare_benchmark_bars(benchmark_bars if benchmark_bars is not None else daily_bars, cfg)

    out = stock.sort_values(["code", "date"]).reset_index(drop=True)

    # Moving averages.
    out[f"ma{cfg.ma_short_window}"] = _rolling_by_code(out, "close", cfg.ma_short_window, "mean")
    out[f"ma{cfg.ma_long_window}"] = _rolling_by_code(out, "close", cfg.ma_long_window, "mean")

    # 20-day stock return.
    out[f"ret{cfg.return_window}"] = (
        out.groupby("code", group_keys=False)["close"].pct_change(periods=cfg.return_window)
    )

    # 10-day recent high and positive drawdown magnitude from that high.
    high_col = f"high_{cfg.high_lookback_window}d"
    drawdown_col = f"drawdown_from_{cfg.high_lookback_window}d_high"
    out[high_col] = _rolling_by_code(out, "high", cfg.high_lookback_window, "max")
    out[drawdown_col] = 1.0 - out["close"] / out[high_col]

    # 5-day average volume.
    vol_ma_col = f"vol_ma{cfg.volume_ma_window}"
    out[vol_ma_col] = _rolling_by_code(out, "volume", cfg.volume_ma_window, "mean")
    out[f"volume_ratio_to_ma{cfg.volume_ma_window}"] = out["volume"] / out[vol_ma_col]

    # Benchmark return and excess return.
    bench_ret = _benchmark_returns(benchmark, cfg)
    out = out.merge(bench_ret, on="date", how="left")
    ret_col = f"ret{cfg.return_window}"
    bench_ret_col = f"benchmark_ret{cfg.return_window}"
    excess_col = f"excess_ret{cfg.return_window}"
    out[excess_col] = out[ret_col] - out[bench_ret_col]
    out[f"relative_strength_{cfg.return_window}d"] = out[excess_col]

    # Convenience boolean fields used by the next signal layer. These are not
    # trading decisions by themselves.
    ma_short_col = f"ma{cfg.ma_short_window}"
    ma_long_col = f"ma{cfg.ma_long_window}"
    out[f"close_above_ma{cfg.ma_short_window}"] = out["close"] > out[ma_short_col]
    out[f"ma{cfg.ma_short_window}_above_ma{cfg.ma_long_window}"] = out[ma_short_col] > out[ma_long_col]
    out[f"volume_below_ma{cfg.volume_ma_window}"] = out["volume"] < out[vol_ma_col]

    required_indicator_cols = [
        ma_short_col,
        ma_long_col,
        ret_col,
        bench_ret_col,
        excess_col,
        high_col,
        drawdown_col,
        vol_ma_col,
    ]
    out["has_complete_indicator_window"] = out[required_indicator_cols].notna().all(axis=1)
    out["benchmark_code"] = cfg.benchmark_code

    return out.sort_values(["code", "date"]).reset_index(drop=True)


def write_indicators(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        df.to_parquet(path, index=False)
    return path


def write_a_share_indicators_from_parquet(
    daily_path: str | Path,
    output_path: str | Path,
    *,
    benchmark_bars: pd.DataFrame | None = None,
    config: AShareIndicatorConfig | None = None,
    batch_size: int = 250_000,
) -> tuple[int, int]:
    """Compute indicators in bounded-memory batches from canonical Parquet."""
    cfg = config or AShareIndicatorConfig()
    benchmark = _prepare_benchmark_bars(benchmark_bars, cfg) if benchmark_bars is not None else None
    if benchmark is None:
        raise ValueError("benchmark_bars is required for streaming indicator computation")

    parquet = pq.ParquetFile(daily_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.tmp")
    writer: pq.ParquetWriter | None = None
    tails: dict[str, pd.DataFrame] = {}
    row_id = 0
    rows_written = 0
    complete_rows = 0
    try:
        for batch in parquet.iter_batches(batch_size=batch_size):
            current = _prepare_stock_bars(batch.to_pandas())
            current["_stream_row_id"] = range(row_id, row_id + len(current))
            row_id += len(current)
            pieces: list[pd.DataFrame] = []
            for code, group in current.groupby("code", sort=False):
                group = group.sort_values("date")
                history = tails.get(code)
                working = pd.concat([history, group], ignore_index=True) if history is not None else group
                calculated = build_a_share_indicators(working, benchmark_bars=benchmark, config=cfg)
                pieces.append(calculated.loc[calculated["_stream_row_id"].isin(group["_stream_row_id"])])
                tails[code] = working.tail(max(cfg.ma_long_window, cfg.return_window, cfg.high_lookback_window, cfg.volume_ma_window))
            if not pieces:
                continue
            # Preserve source code/date order so companion streamed datasets can be joined by key.
            result = pd.concat(pieces, ignore_index=True)
            result = result.drop(columns=["_stream_row_id"])
            table = pa.Table.from_pandas(result, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp, table.schema, compression="snappy")
            writer.write_table(table)
            rows_written += len(result)
            complete_rows += int(result["has_complete_indicator_window"].sum())
        if writer is None:
            raise ValueError("daily_bars contained no rows")
        os.replace(temp, output)
        return rows_written, complete_rows
    finally:
        if writer is not None:
            writer.close()
        if temp.exists():
            temp.unlink()


def _prepare_stock_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("daily_bars is empty")
    required = {"date", "code", "close", "high", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"daily_bars missing required columns: {missing}")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["code"] = out["code"].astype(str)
    _select_adjusted_price_layer(out, required=("close", "high"))
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.drop_duplicates(["date", "code"], keep="last")
    return out


def _prepare_benchmark_bars(df: pd.DataFrame, cfg: AShareIndicatorConfig) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("benchmark_bars is empty")
    required = {"date", "code", "close"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"benchmark_bars missing required columns: {missing}")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["code"] = out["code"].astype(str)
    _select_adjusted_price_layer(out, required=("close",))
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.loc[out["code"] == cfg.benchmark_code].copy()
    if out.empty:
        raise ValueError(f"benchmark code not found: {cfg.benchmark_code}")
    out = out.drop_duplicates(["date", "code"], keep="last")
    return out.sort_values("date").reset_index(drop=True)


def _select_adjusted_price_layer(df: pd.DataFrame, *, required: tuple[str, ...]) -> None:
    explicit = all(f"adj_{field}" in df and df[f"adj_{field}"].notna().all() for field in required)
    if explicit:
        for field in ("open", "high", "low", "close"):
            adjusted = f"adj_{field}"
            if adjusted in df:
                df[field] = df[adjusted]
        return
    if "adj_type" not in df or not df["adj_type"].astype(str).str.lower().isin({"qfq", "hfq"}).all():
        raise ValueError("adjusted price layer required for indicator calculation")


def _benchmark_returns(benchmark: pd.DataFrame, cfg: AShareIndicatorConfig) -> pd.DataFrame:
    out = benchmark[["date", "close"]].sort_values("date").copy()
    out = out.rename(columns={"close": "benchmark_close"})
    out[f"benchmark_ret{cfg.return_window}"] = out["benchmark_close"].pct_change(periods=cfg.return_window)
    return out[["date", "benchmark_close", f"benchmark_ret{cfg.return_window}"]]


def _rolling_by_code(df: pd.DataFrame, column: str, window: int, kind: str) -> pd.Series:
    grouped = df.groupby("code", group_keys=False)[column]
    if kind == "mean":
        return grouped.transform(lambda s: s.rolling(window=window, min_periods=window).mean())
    if kind == "max":
        return grouped.transform(lambda s: s.rolling(window=window, min_periods=window).max())
    raise ValueError(f"unsupported rolling kind: {kind}")

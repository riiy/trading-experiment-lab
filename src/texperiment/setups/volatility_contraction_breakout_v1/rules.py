from __future__ import annotations

from dataclasses import dataclass
import os
from itertools import zip_longest
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


SIGNAL_COLUMNS = [
    "signal_id", "setup_id", "code", "name", "signal_date", "trigger_date", "status",
    "entry_execution", "atr10", "atr10_median60", "amplitude10", "prior_amplitude10",
    "prior_high20", "volume_ratio10", "stop_distance_atr", "invalid_reason",
]


@dataclass(frozen=True)
class VolatilityContractionBreakoutConfig:
    setup_id: str = "VOLATILITY_CONTRACTION_BREAKOUT_v1"
    atr_window: int = 10
    atr_median_window: int = 60
    amplitude_window: int = 10
    breakout_window: int = 20
    volume_ratio_window: int = 10
    stop_atr_multiple: float = 2.0

    @classmethod
    def from_setup_config(cls, setup_config: dict[str, Any] | None = None) -> "VolatilityContractionBreakoutConfig":
        cfg = setup_config or {}
        signal = cfg.get("signal", {})
        exit_cfg = cfg.get("exit", {})
        return cls(
            setup_id=str(cfg.get("setup_id", cls.setup_id)),
            atr_window=int(signal.get("atr_window", 10)),
            atr_median_window=int(signal.get("atr_median_window", 60)),
            amplitude_window=int(signal.get("amplitude_window", 10)),
            breakout_window=int(signal.get("breakout_window", 20)),
            volume_ratio_window=int(signal.get("volume_ratio_window", 10)),
            stop_atr_multiple=float(exit_cfg.get("initial_stop_atr_multiple", 2.0)),
        )


def build_volatility_contraction_breakout_signals(
    daily_bars: pd.DataFrame,
    *,
    universe: pd.DataFrame | None = None,
    setup_config: dict[str, Any] | None = None,
    include_candidates: bool = False,
) -> pd.DataFrame:
    """Generate close-of-day contraction/breakout signals using qfq structural prices.

    ATR and all pattern tests use adjusted prices.  Execution is intentionally left
    to the downstream raw-price backtest.  The 60-day ATR median, previous 20-day
    high, and comparison amplitude are shifted one day to prevent look-ahead.
    """
    cfg = VolatilityContractionBreakoutConfig.from_setup_config(setup_config)
    bars = _prepare_structural_bars(daily_bars)
    if universe is not None:
        bars = _attach_universe(bars, universe)
    else:
        bars["is_tradable_universe"] = True

    rows: list[dict[str, Any]] = []
    for code, group in bars.groupby("code", sort=True):
        g = group.sort_values("date").copy()
        previous_close = g["adj_close"].shift(1)
        true_range = pd.concat([
            g["adj_high"] - g["adj_low"],
            (g["adj_high"] - previous_close).abs(),
            (g["adj_low"] - previous_close).abs(),
        ], axis=1).max(axis=1)
        g["atr10"] = true_range.rolling(cfg.atr_window, min_periods=cfg.atr_window).mean()
        g["atr10_median60"] = g["atr10"].rolling(cfg.atr_median_window, min_periods=cfg.atr_median_window).median().shift(1)
        rolling_high = g["adj_high"].rolling(cfg.amplitude_window, min_periods=cfg.amplitude_window).max()
        rolling_low = g["adj_low"].rolling(cfg.amplitude_window, min_periods=cfg.amplitude_window).min()
        g["amplitude10"] = rolling_high / rolling_low - 1.0
        g["prior_amplitude10"] = g["amplitude10"].shift(cfg.amplitude_window)
        g["prior_high20"] = g["adj_high"].rolling(cfg.breakout_window, min_periods=cfg.breakout_window).max().shift(1)
        g["volume_ratio10"] = g["volume"] / g["volume"].rolling(cfg.volume_ratio_window, min_periods=cfg.volume_ratio_window).mean().shift(1)

        for row in g.itertuples(index=False):
            values = row._asdict()
            complete = all(pd.notna(values.get(key)) for key in ("atr10", "atr10_median60", "amplitude10", "prior_amplitude10", "prior_high20", "volume_ratio10"))
            pattern = complete and bool(
                values["atr10"] < values["atr10_median60"]
                and values["amplitude10"] < values["prior_amplitude10"]
                and values["adj_close"] > values["prior_high20"]
                and values["volume_ratio10"] > 1.0
            )
            executable = bool(values.get("is_tradable_universe", False))
            triggered = pattern and executable
            if not triggered and not include_candidates:
                continue
            reason = None
            if not complete:
                reason = "insufficient_indicator_history"
            elif not executable:
                reason = "not_tradable_universe"
            elif not pattern:
                reason = "contraction_or_breakout_rule_not_met"
            date = pd.Timestamp(values["date"]).strftime("%Y-%m-%d")
            rows.append({
                "signal_id": f"{cfg.setup_id}:{code}:{date}", "setup_id": cfg.setup_id,
                "code": code, "name": values.get("name"), "signal_date": date, "trigger_date": date,
                "status": "triggered_entry_next_open" if triggered else "candidate_rejected",
                "entry_execution": "next_day_open", "atr10": _float(values.get("atr10")),
                "atr10_median60": _float(values.get("atr10_median60")), "amplitude10": _float(values.get("amplitude10")),
                "prior_amplitude10": _float(values.get("prior_amplitude10")), "prior_high20": _float(values.get("prior_high20")),
                "volume_ratio10": _float(values.get("volume_ratio10")), "stop_distance_atr": cfg.stop_atr_multiple,
                "invalid_reason": reason,
            })
    out = pd.DataFrame(rows)
    for col in SIGNAL_COLUMNS:
        if col not in out:
            out[col] = pd.NA
    return out[SIGNAL_COLUMNS].sort_values(["code", "signal_date"]).reset_index(drop=True)


def write_volatility_contraction_breakout_signals_from_parquet(
    daily_path: str | Path,
    universe_path: str | Path,
    output_path: str | Path,
    *,
    setup_config: dict[str, Any] | None = None,
    batch_size: int = 250_000,
) -> int:
    """Generate triggered VCB signals in bounded memory from paired daily bars."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    daily_file, universe_file, output = pq.ParquetFile(daily_path), pq.ParquetFile(universe_path), Path(output_path)
    if output.exists():
        raise FileExistsError(f"VCB signals output already exists: {output}")
    daily_columns = [name for name in ("date", "code", "name", "adj_type", "adj_high", "adj_low", "adj_close", "volume") if name in daily_file.schema_arrow.names]
    required_daily = {"date", "code", "adj_high", "adj_low", "adj_close", "volume"}
    missing = sorted(required_daily - set(daily_columns))
    if missing:
        raise ValueError(f"paired daily input missing signal columns: {missing}")
    required_universe = {"date", "code", "is_tradable_universe"}
    missing = sorted(required_universe - set(universe_file.schema_arrow.names))
    if missing:
        raise ValueError(f"universe input missing signal columns: {missing}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    tails: dict[str, pd.DataFrame] = {}
    written = 0
    try:
        daily_batches = daily_file.iter_batches(batch_size=batch_size, columns=daily_columns)
        universe_batches = universe_file.iter_batches(batch_size=batch_size, columns=list(required_universe))
        for daily_batch, universe_batch in zip_longest(daily_batches, universe_batches):
            if daily_batch is None or universe_batch is None:
                raise ValueError("daily/universe batch counts differ")
            current = daily_batch.to_pandas()
            universe = universe_batch.to_pandas()
            if len(current) != len(universe) or not current[["date", "code"]].equals(universe[["date", "code"]]):
                raise ValueError("daily/universe primary keys differ")
            current["is_tradable_universe"] = universe["is_tradable_universe"].fillna(False).astype(bool)
            current["_is_current"] = True
            pieces: list[pd.DataFrame] = []
            for code, group in current.groupby("code", sort=False):
                history = tails.get(str(code))
                working = pd.concat([history, group], ignore_index=True) if history is not None else group.copy()
                signals = build_volatility_contraction_breakout_signals(
                    working.drop(columns="is_tradable_universe"),
                    universe=working[["date", "code", "is_tradable_universe"]],
                    setup_config=setup_config,
                )
                if not signals.empty:
                    current_dates = set(pd.to_datetime(group["date"]).dt.strftime("%Y-%m-%d"))
                    pieces.append(signals.loc[signals["signal_date"].isin(current_dates)])
                tails[str(code)] = working.tail(90).assign(_is_current=False)
            if not pieces:
                continue
            result = pd.concat(pieces, ignore_index=True).sort_values(["code", "signal_date"])
            table = pa.Table.from_pandas(result, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp, table.schema, compression="snappy")
            writer.write_table(table)
            written += len(result)
        if writer is None:
            table = pa.Table.from_pandas(pd.DataFrame(columns=SIGNAL_COLUMNS), preserve_index=False)
            writer = pq.ParquetWriter(temp, table.schema, compression="snappy")
        writer.close()
        writer = None
        os.replace(temp, output)
        return written
    finally:
        if writer is not None:
            writer.close()
        if temp.exists():
            temp.unlink()


def _prepare_structural_bars(daily_bars: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "code", "volume"}
    missing = sorted(required - set(daily_bars.columns))
    if missing:
        raise ValueError(f"daily_bars missing required columns: {missing}")
    out = daily_bars.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out["code"] = out["code"].astype(str)
    qfq_declared = "adj_type" in out.columns and out["adj_type"].astype(str).str.lower().eq("qfq").all()
    for field in ("high", "low", "close"):
        adjusted = f"adj_{field}"
        source = adjusted if adjusted in out.columns and out[adjusted].notna().all() else field if qfq_declared else None
        if source not in out:
            raise ValueError(f"qfq structural price missing: {adjusted}")
        out[adjusted] = pd.to_numeric(out[source], errors="coerce")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce")
    return out.dropna(subset=["date", "adj_high", "adj_low", "adj_close", "volume"]).drop_duplicates(["date", "code"], keep="last")


def _attach_universe(bars: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "code", "is_tradable_universe"}
    missing = sorted(required - set(universe.columns))
    if missing:
        raise ValueError(f"universe missing required columns: {missing}")
    uni = universe[["date", "code", "is_tradable_universe"]].copy()
    uni["date"] = pd.to_datetime(uni["date"], errors="coerce").dt.normalize()
    uni["code"] = uni["code"].astype(str)
    return bars.merge(uni.drop_duplicates(["date", "code"], keep="last"), on=["date", "code"], how="left").assign(
        is_tradable_universe=lambda df: df["is_tradable_universe"].fillna(False).astype(bool)
    )


def _float(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)

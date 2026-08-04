from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def write_development_backtest_bars(
    daily_path: str | Path,
    signals_path: str | Path,
    output_path: str | Path,
    *,
    batch_size: int = 250_000,
    lookback_days: int = 15,
    forward_days: int = 30,
) -> dict[str, int]:
    """Materialize only bars needed to evaluate development-period signals."""
    daily_path, signals_path, output_path = Path(daily_path), Path(signals_path), Path(output_path)
    signals = pd.read_parquet(signals_path)
    required_signal = {"code", "signal_date", "status"}
    missing = sorted(required_signal - set(signals.columns))
    if missing:
        raise ValueError(f"signals missing required columns: {missing}")
    signals = signals.loc[signals["status"].eq("triggered_entry_next_open")].copy()
    signals["code"] = signals["code"].astype(str)
    signals["signal_date"] = pd.to_datetime(signals["signal_date"], errors="coerce").dt.normalize()
    signals = signals.dropna(subset=["signal_date"])
    if signals.empty:
        raise ValueError("development signals contain no triggered rows")

    parquet = pq.ParquetFile(daily_path)
    date_values: set[pd.Timestamp] = set()
    for batch in parquet.iter_batches(batch_size=batch_size, columns=["date"]):
        dates = pd.to_datetime(batch.column("date").to_pandas(), errors="coerce").dt.normalize().dropna()
        date_values.update(dates.tolist())
    calendar = pd.DatetimeIndex(sorted(date_values))
    if calendar.empty:
        raise ValueError("daily input has no valid dates")
    positions = pd.Series(range(len(calendar)), index=calendar)
    windows: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for code, frame in signals.groupby("code", sort=False):
        lo, hi = frame["signal_date"].min(), frame["signal_date"].max()
        lo_pos = max(0, int(positions.get(lo, 0)) - lookback_days)
        hi_pos = min(len(calendar) - 1, int(positions.get(hi, len(calendar) - 1)) + forward_days)
        windows[str(code)] = (calendar[lo_pos], calendar[hi_pos])
    window_frame = pd.DataFrame(
        [(code, start, end) for code, (start, end) in windows.items()],
        columns=["code", "window_start", "window_end"],
    )

    columns = [
        "date", "code", "name", "board", "listing_date", "listing_trading_day",
        "raw_open", "raw_high", "raw_low", "raw_close", "raw_pre_close",
        "adj_open", "adj_high", "adj_low", "adj_close", "adj_factor", "adj_offset",
        "volume", "amount", "is_suspended",
    ]
    tmp = output_path.with_name(output_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    writer = None
    rows = 0
    try:
        for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
            frame = batch.to_pandas()
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
            frame["code"] = frame["code"].astype(str)
            candidate = frame.merge(window_frame, on="code", how="inner", sort=False)
            selected = candidate.loc[candidate["date"].between(candidate["window_start"], candidate["window_end"])]
            selected = selected.drop(columns=["window_start", "window_end"])
            if selected.empty:
                continue
            table = pa.Table.from_pandas(selected, preserve_index=False)
            if writer is None:
                tmp.parent.mkdir(parents=True, exist_ok=True)
                writer = pq.ParquetWriter(tmp, table.schema, compression="zstd")
            writer.write_table(table)
            rows += len(selected)
    finally:
        if writer is not None:
            writer.close()
    if rows == 0:
        tmp.unlink(missing_ok=True)
        raise ValueError("no daily bars matched development signal windows")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(output_path)
    return {"rows": rows, "codes": len(windows), "signals": len(signals)}

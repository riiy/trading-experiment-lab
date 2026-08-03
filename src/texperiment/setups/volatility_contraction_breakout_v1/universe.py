from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.universe.a_share import AShareUniverseConfig


VCB_UNIVERSE_COLUMNS = ["date", "code", "is_tradable_universe"]
_INPUT_COLUMNS = [
    "date", "code", "close", "raw_close", "amount", "volume", "historical_st_status",
    "is_suspended", "trade_status", "one_price_limit_up", "one_price_limit_down", "listing_days", "listing_date",
]


def write_volatility_contraction_breakout_universe_from_parquet(
    daily_path: str | Path,
    output_path: str | Path,
    *,
    setup_config: dict[str, Any],
    batch_size: int = 100_000,
) -> tuple[int, int]:
    """Write only the VCB signal-layer universe keys in bounded memory."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    cfg = AShareUniverseConfig.from_setup_config(setup_config)
    source = pq.ParquetFile(daily_path)
    columns = [name for name in _INPUT_COLUMNS if name in source.schema_arrow.names]
    required = {"date", "code", "close", "raw_close", "amount"}
    missing = sorted(required - set(columns))
    if missing:
        raise ValueError(f"daily input missing universe columns: {missing}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    tails: dict[str, pd.DataFrame] = {}
    written = eligible = 0
    try:
        for batch in source.iter_batches(batch_size=batch_size, columns=columns):
            current = batch.to_pandas()
            current["_is_current"] = True
            pieces: list[pd.DataFrame] = []
            for code, group in current.groupby("code", sort=False):
                group = group.sort_values("date").copy()
                history = tails.get(str(code))
                working = pd.concat([history, group], ignore_index=True) if history is not None else group
                annotated = _annotate_vcb_universe(working, cfg)
                selected = annotated.loc[annotated["_is_current"].fillna(False), VCB_UNIVERSE_COLUMNS]
                pieces.append(selected)
                tails[str(code)] = working.tail(20).assign(_is_current=False)
            if not pieces:
                continue
            result = pd.concat(pieces, ignore_index=True)
            table = pa.Table.from_pandas(result, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp, table.schema, compression="snappy")
            writer.write_table(table)
            written += len(result)
            eligible += int(result["is_tradable_universe"].sum())
        if writer is None:
            raise ValueError("daily input contained no rows")
        writer.close()
        writer = None
        os.replace(temp, output)
        return written, eligible
    finally:
        if writer is not None:
            writer.close()
        if temp.exists():
            temp.unlink()


def _annotate_vcb_universe(frame: pd.DataFrame, cfg: AShareUniverseConfig) -> pd.DataFrame:
    """Vectorized subset of the shared A-share universe policy for VCB keys."""
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out["code"] = out["code"].astype(str)
    raw_close = pd.to_numeric(out["raw_close"], errors="coerce")
    amount = pd.to_numeric(out["amount"], errors="coerce")
    volume = pd.to_numeric(out.get("volume"), errors="coerce")
    avg_amount = amount.rolling(window=20, min_periods=20).mean()
    historical_st = out.get("historical_st_status", pd.Series("UNKNOWN", index=out.index)).astype(str).str.upper()
    suspended = _bool(out.get("is_suspended", pd.Series(False, index=out.index)))
    trade_status = out.get("trade_status", pd.Series("", index=out.index)).astype(str).str.strip().str.lower()
    suspended = suspended | trade_status.isin({"0", "停牌", "suspended", "halt", "halted"}) | ((volume.fillna(0) <= 0) & (amount.fillna(0) <= 0))
    listing_days = pd.to_numeric(out.get("listing_days"), errors="coerce")
    if not listing_days.notna().any() and "listing_date" in out:
        listing_days = (out["date"] - pd.to_datetime(out["listing_date"], errors="coerce").dt.normalize()).dt.days + 1
    one_price_up = out.get("one_price_limit_up", pd.Series("UNKNOWN", index=out.index)).astype(str).str.upper()
    one_price_down = out.get("one_price_limit_down", pd.Series("UNKNOWN", index=out.index)).astype(str).str.upper()
    eligible = (
        (historical_st.eq("FALSE") if cfg.exclude_st else True)
        & (listing_days >= cfg.min_listing_days).fillna(False)
        & ((~suspended) if cfg.exclude_suspended else True)
        & ((one_price_up.eq("FALSE") & one_price_down.eq("FALSE")) if cfg.exclude_limit_up_down else True)
        & (avg_amount >= cfg.min_avg_amount_20d).fillna(False)
        & (raw_close * cfg.lot_size <= cfg.max_one_lot_value).fillna(False)
        & ~out["code"].isin(cfg.data_quality_excluded_codes)
    )
    return out.assign(is_tradable_universe=eligible.astype(bool))


def _bool(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values.fillna(False).astype(bool)
    return values.astype(str).str.strip().str.lower().isin({"1", "true", "t", "yes", "y", "是"})

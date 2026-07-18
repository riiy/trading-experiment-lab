from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class AShareUniverseConfig:
    """Executable A-share universe constraints for the Trading Experiment account."""

    min_listing_days: int = 180
    min_avg_amount_20d: float = 300_000_000
    max_one_lot_value: float = 15_000
    lot_size: int = 100
    exclude_st: bool = True
    exclude_suspended: bool = True
    exclude_limit_up_down: bool = True

    @classmethod
    def from_setup_config(cls, setup_config: dict[str, Any]) -> "AShareUniverseConfig":
        universe = setup_config.get("universe", setup_config)
        return cls(
            min_listing_days=int(universe.get("exclude_new_listing_days_lt", universe.get("min_listing_days", 180))),
            min_avg_amount_20d=float(universe.get("min_avg_amount_20d", 300_000_000)),
            max_one_lot_value=float(universe.get("max_one_lot_value", 15_000)),
            lot_size=int(universe.get("lot_size", 100)),
            exclude_st=bool(universe.get("exclude_st", True)),
            exclude_suspended=bool(universe.get("exclude_suspended", True)),
            exclude_limit_up_down=bool(universe.get("exclude_limit_up_down", True)),
        )


def build_a_share_universe(
    daily_bars: pd.DataFrame,
    *,
    as_of_date: str | pd.Timestamp | None = None,
    config: AShareUniverseConfig | None = None,
    include_rejected: bool = False,
) -> pd.DataFrame:
    """Build an executable A-share universe from canonical daily bars.

    Parameters
    ----------
    daily_bars:
        Canonical daily bars with at least ``date``, ``code``, ``close`` and ``amount``.
    as_of_date:
        Optional trading date. If supplied, only the latest row per stock on this date is
        evaluated. If omitted, every stock-date row is annotated and filtered.
    config:
        Universe constraints. Defaults match ``STOCK_RS_PULLBACK_v1``.
    include_rejected:
        Return all annotated rows instead of eligible rows only.
    """
    cfg = config or AShareUniverseConfig()
    annotated = annotate_a_share_universe(daily_bars, as_of_date=as_of_date, config=cfg)
    if include_rejected:
        return annotated.reset_index(drop=True)
    return annotated.loc[annotated["is_tradable_universe"]].reset_index(drop=True)


def filter_a_share_universe(
    df: pd.DataFrame,
    *,
    min_listing_days: int = 180,
    min_avg_amount_20d: float = 300_000_000,
    max_one_lot_value: float = 15_000,
    lot_size: int = 100,
) -> pd.DataFrame:
    """Backward-compatible wrapper used by older tests and scripts.

    The function now derives missing ``avg_amount_20d``, ``listing_days`` and risk flags
    from canonical daily bars when possible, then returns eligible rows only.
    """
    return build_a_share_universe(
        df,
        config=AShareUniverseConfig(
            min_listing_days=min_listing_days,
            min_avg_amount_20d=min_avg_amount_20d,
            max_one_lot_value=max_one_lot_value,
            lot_size=lot_size,
        ),
        include_rejected=False,
    )


def annotate_a_share_universe(
    daily_bars: pd.DataFrame,
    *,
    as_of_date: str | pd.Timestamp | None = None,
    config: AShareUniverseConfig | None = None,
) -> pd.DataFrame:
    """Annotate A-share rows with universe filter pass/fail columns.

    Output includes:
    - ``avg_amount_20d``
    - ``one_lot_value``
    - ``pass_non_st``
    - ``pass_listing_days``
    - ``pass_not_suspended``
    - ``pass_not_limit_up_down``
    - ``pass_avg_amount_20d``
    - ``pass_one_lot_value``
    - ``is_tradable_universe``
    - ``reject_reasons``
    """
    cfg = config or AShareUniverseConfig()
    out = _prepare_daily_bars(daily_bars)

    if as_of_date is not None:
        as_of = pd.Timestamp(as_of_date).normalize()
        out = out.loc[out["date"] <= as_of].copy()
        if out.empty:
            return _empty_universe_frame(daily_bars)

    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    historical_st = out.get("historical_st_status", pd.Series("UNKNOWN", index=out.index)).astype(str).str.upper()
    out["is_st"] = historical_st.eq("TRUE")
    out["is_suspended"] = _derive_suspended(out)
    out["is_limit_up"] = out.get("close_at_limit_up", pd.Series("UNKNOWN", index=out.index)).astype(str).str.upper().eq("TRUE")
    out["is_limit_down"] = out.get("close_at_limit_down", pd.Series("UNKNOWN", index=out.index)).astype(str).str.upper().eq("TRUE")
    out["listing_days"] = _derive_listing_days(out)
    out["avg_amount_20d"] = _derive_avg_amount_20d(out)
    out["one_lot_value"] = pd.to_numeric(out.get("raw_close"), errors="coerce") * cfg.lot_size

    if as_of_date is not None:
        as_of = pd.Timestamp(as_of_date).normalize()
        out = out.loc[out["date"] == as_of].copy()
        if out.empty:
            return _empty_universe_frame(daily_bars)

    out["pass_non_st"] = historical_st.eq("FALSE") if cfg.exclude_st else True
    out["pass_listing_days"] = (out["listing_days"] >= cfg.min_listing_days).fillna(False)
    out["pass_not_suspended"] = ~out["is_suspended"] if cfg.exclude_suspended else True
    if cfg.exclude_limit_up_down:
        one_price_up = out.get("one_price_limit_up", pd.Series("UNKNOWN", index=out.index)).astype(str).str.upper()
        one_price_down = out.get("one_price_limit_down", pd.Series("UNKNOWN", index=out.index)).astype(str).str.upper()
        out["pass_not_limit_up_down"] = one_price_up.eq("FALSE") & one_price_down.eq("FALSE")
    else:
        out["pass_not_limit_up_down"] = True
    out["pass_avg_amount_20d"] = (out["avg_amount_20d"] >= cfg.min_avg_amount_20d).fillna(False)
    out["pass_one_lot_value"] = (out["one_lot_value"] <= cfg.max_one_lot_value).fillna(False)

    pass_cols = [
        "pass_non_st",
        "pass_listing_days",
        "pass_not_suspended",
        "pass_not_limit_up_down",
        "pass_avg_amount_20d",
        "pass_one_lot_value",
    ]
    out["is_tradable_universe"] = out[pass_cols].all(axis=1)
    out["reject_reasons"] = out.apply(_reject_reasons, axis=1)
    return out.reset_index(drop=True)


def write_universe(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return
    df.to_parquet(path, index=False)


def write_a_share_universe_from_parquet(
    daily_path: str | Path,
    output_path: str | Path,
    *,
    as_of_date: str | pd.Timestamp | None = None,
    config: AShareUniverseConfig | None = None,
    include_rejected: bool = False,
    batch_size: int = 250_000,
) -> tuple[int, int]:
    """Build an A-share universe from Parquet without loading all bars."""
    cfg = config or AShareUniverseConfig()
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date is not None else None
    parquet = pq.ParquetFile(daily_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    tails: dict[str, pd.DataFrame] = {}
    row_id = 0
    rows_written = 0
    eligible_count = 0
    try:
        for batch in parquet.iter_batches(batch_size=batch_size):
            current = _prepare_daily_bars(batch.to_pandas())
            current["_stream_row_id"] = range(row_id, row_id + len(current))
            row_id += len(current)
            pieces: list[pd.DataFrame] = []
            for code, group in current.groupby("code", sort=False):
                group = group.sort_values("date").copy()
                history = tails.get(code)
                working = pd.concat([history, group], ignore_index=True) if history is not None else group
                annotated = annotate_a_share_universe(working, config=cfg)
                selected = annotated.loc[annotated["_stream_row_id"].isin(group["_stream_row_id"])].copy()
                if as_of is not None:
                    selected = selected.loc[selected["date"] == as_of]
                if not include_rejected:
                    selected = selected.loc[selected["is_tradable_universe"]]
                if not selected.empty:
                    pieces.append(selected)
                tails[code] = working.tail(20)
            if not pieces:
                continue
            result = pd.concat(pieces, ignore_index=True).drop(columns=["_stream_row_id"])
            table = pa.Table.from_pandas(result, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp, table.schema, compression="snappy")
            writer.write_table(table)
            rows_written += len(result)
            eligible_count += int(result["is_tradable_universe"].sum())

        if writer is not None:
            writer.close()
            writer = None
            os.replace(temp, output)
        else:
            empty = _empty_universe_frame(pd.DataFrame())
            write_universe(empty, output)
        return rows_written, eligible_count
    finally:
        if writer is not None:
            writer.close()
        if temp.exists():
            temp.unlink()


def _prepare_daily_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    required = {"code", "close"}
    if "amount" not in df.columns and "avg_amount_20d" not in df.columns:
        required.add("amount")
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"daily_bars missing required columns: {missing}")
    out = df.copy()
    if "date" not in out.columns:
        out["date"] = pd.Timestamp("1970-01-01")
    else:
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["code"] = out["code"].astype(str)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    if "raw_close" not in out:
        out["raw_close"] = pd.NA
    out["raw_close"] = pd.to_numeric(out["raw_close"], errors="coerce")
    if "amount" in out.columns:
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce")
    else:
        out["amount"] = pd.NA
    if "volume" in out.columns:
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce")
    return out


def _derive_bool_column(df: pd.DataFrame, column: str, *, default: bool) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype=bool)
    values = df[column]
    if values.dtype == bool:
        return values.fillna(default).astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    true_values = {"1", "true", "t", "yes", "y", "是", "st", "*st"}
    false_values = {"0", "false", "f", "no", "n", "否", "nan", "none", ""}
    return normalized.map(lambda x: True if x in true_values else False if x in false_values else bool(x)).astype(bool)


def _derive_st_from_name(df: pd.DataFrame) -> pd.Series:
    if "name" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    names = df["name"].fillna("").astype(str).str.upper()
    return names.str.contains(r"(?:^|[^A-Z])(?:\*?ST|退)", regex=True)


def _derive_suspended(df: pd.DataFrame) -> pd.Series:
    suspended = _derive_bool_column(df, "is_suspended", default=False)
    if "trade_status" in df.columns:
        status = df["trade_status"].fillna("").astype(str).str.strip().str.lower()
        suspended = suspended | status.isin({"0", "停牌", "suspended", "halt", "halted"})
    if {"volume", "amount"}.issubset(df.columns):
        suspended = suspended | ((df["volume"].fillna(0) <= 0) & (df["amount"].fillna(0) <= 0))
    return suspended.astype(bool)


def _derive_listing_days(df: pd.DataFrame) -> pd.Series:
    if "listing_days" in df.columns:
        listing_days = pd.to_numeric(df["listing_days"], errors="coerce")
        if listing_days.notna().any():
            return listing_days.astype("Float64")
    if "listing_date" in df.columns:
        listing_date = pd.to_datetime(df["listing_date"], errors="coerce").dt.normalize()
        return ((df["date"] - listing_date).dt.days + 1).where(listing_date.notna()).astype("Float64")
    return pd.Series(pd.NA, index=df.index, dtype="Float64")


def _derive_avg_amount_20d(df: pd.DataFrame) -> pd.Series:
    fallback = None
    if "amount" in df.columns and pd.to_numeric(df["amount"], errors="coerce").notna().any():
        fallback = (
            df.groupby("code", group_keys=False)["amount"]
            .rolling(window=20, min_periods=20)
            .mean()
            .reset_index(level=0, drop=True)
        )
    if "avg_amount_20d" in df.columns:
        existing = pd.to_numeric(df["avg_amount_20d"], errors="coerce")
        if fallback is not None:
            return existing.fillna(fallback)
        return existing
    if fallback is not None:
        return fallback
    return pd.Series(pd.NA, index=df.index, dtype="Float64")


def _reject_reasons(row: pd.Series) -> str:
    reasons: list[str] = []
    mapping = {
        "pass_non_st": "st_or_star_st",
        "pass_listing_days": "listing_days_lt_min",
        "pass_not_suspended": "suspended_or_no_trade",
        "pass_not_limit_up_down": "limit_up_or_limit_down",
        "pass_avg_amount_20d": "avg_amount_20d_below_min",
        "pass_one_lot_value": "one_lot_value_above_max",
    }
    for col, reason in mapping.items():
        if not bool(row[col]):
            reasons.append(reason)
    return ";".join(reasons)


def _empty_universe_frame(source: pd.DataFrame) -> pd.DataFrame:
    columns = list(source.columns)
    for col in [
        "avg_amount_20d",
        "one_lot_value",
        "pass_non_st",
        "pass_listing_days",
        "pass_not_suspended",
        "pass_not_limit_up_down",
        "pass_avg_amount_20d",
        "pass_one_lot_value",
        "is_tradable_universe",
        "reject_reasons",
    ]:
        if col not in columns:
            columns.append(col)
    return pd.DataFrame(columns=columns)

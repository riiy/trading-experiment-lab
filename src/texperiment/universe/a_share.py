from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
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
    require_st_metadata: bool = True

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


def build_a_share_universe_from_parquet(
    path: str | Path,
    *,
    as_of_date: str | pd.Timestamp | None = None,
    config: AShareUniverseConfig | None = None,
    include_rejected: bool = False,
    batch_size: int = 250_000,
) -> pd.DataFrame:
    """Build universe using bounded-memory Parquet batches.

    State retained per code is first valid date, latest row, and last 20 amounts.
    """
    parquet = pq.ParquetFile(path)
    requested = pd.Timestamp(as_of_date).normalize() if as_of_date is not None else None
    states: dict[str, dict[str, Any]] = {}
    for batch in parquet.iter_batches(batch_size=batch_size):
        frame = _prepare_daily_bars(batch.to_pandas())
        if requested is not None:
            frame = frame.loc[frame["date"] <= requested]
        if frame.empty:
            continue
        for code, group in frame.groupby("code", sort=False):
            group = group.sort_values("date")
            valid = group.loc[group["close"].notna() & (group["close"] > 0)]
            if valid.empty:
                continue
            state = states.setdefault(
                code,
                {"first_trade_date": valid["date"].min(), "amounts": []},
            )
            state["first_trade_date"] = min(state["first_trade_date"], valid["date"].min())
            state["amounts"].extend(
                zip(valid["date"].tolist(), pd.to_numeric(valid["amount"], errors="coerce").tolist())
            )
            state["amounts"] = sorted(
                ((date, amount) for date, amount in state["amounts"] if pd.notna(amount)),
                key=lambda item: item[0],
            )[-20:]
            candidate = valid.iloc[-1]
            if "latest" not in state or candidate["date"] >= state["latest"]["date"]:
                state["latest"] = candidate.to_dict()

    if not states:
        return _empty_universe_frame(pd.DataFrame())
    snapshot = pd.DataFrame([state["latest"] | {
        "first_trade_date": state["first_trade_date"],
        "avg_amount_20d": sum(amount for _, amount in state["amounts"]) / 20 if len(state["amounts"]) == 20 else pd.NA,
    } for state in states.values()])
    effective_as_of = snapshot["date"].max()
    annotated = annotate_a_share_universe(snapshot, config=config)
    annotated["effective_as_of"] = effective_as_of
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
    out["st_metadata_available"] = _st_metadata_available(out)
    out["is_st"] = _derive_bool_column(out, "is_st", default=False) | _derive_st_from_name(out)
    out["is_suspended"] = _derive_suspended(out)
    out["board"] = _derive_board(out)
    out["limit_rate"] = _derive_limit_rate(out)
    out["is_limit_up"] = _derive_limit_flag(out, flag_col="is_limit_up", direction="up")
    out["is_limit_down"] = _derive_limit_flag(out, flag_col="is_limit_down", direction="down")
    out["listing_days"] = _derive_listing_days(out)
    out["avg_amount_20d"] = _derive_avg_amount_20d(out)
    out["one_lot_value"] = out["close"] * cfg.lot_size

    if as_of_date is not None:
        as_of = pd.Timestamp(as_of_date).normalize()
        effective_as_of = out["date"].max()
        out = out.loc[out["date"] == effective_as_of].copy()
        if out.empty:
            return _empty_universe_frame(daily_bars)
        out["effective_as_of"] = effective_as_of

    out["pass_non_st"] = (~out["is_st"] & out["st_metadata_available"]) if cfg.exclude_st and cfg.require_st_metadata else (~out["is_st"] if cfg.exclude_st else True)
    out["pass_listing_days"] = out["listing_days"] >= cfg.min_listing_days
    out["pass_not_suspended"] = ~out["is_suspended"] if cfg.exclude_suspended else True
    if cfg.exclude_limit_up_down:
        out["pass_not_limit_up_down"] = ~(out["is_limit_up"] | out["is_limit_down"])
    else:
        out["pass_not_limit_up_down"] = True
    out["pass_avg_amount_20d"] = out["avg_amount_20d"] >= cfg.min_avg_amount_20d
    out["pass_one_lot_value"] = out["one_lot_value"] <= cfg.max_one_lot_value

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


def _st_metadata_available(df: pd.DataFrame) -> pd.Series:
    has_flag = df["is_st"].notna() if "is_st" in df.columns else pd.Series(False, index=df.index)
    has_name = (
        df["name"].fillna("").astype(str).str.strip().ne("")
        if "name" in df.columns
        else pd.Series(False, index=df.index)
    )
    untrusted_tdx = (
        df["source"].fillna("").astype(str).str.lower().isin({"tongdaxin", "tdx"})
        if "source" in df.columns
        else pd.Series(False, index=df.index)
    )
    return (has_name | (has_flag & ~untrusted_tdx & ~has_name)).astype(bool)


def _derive_suspended(df: pd.DataFrame) -> pd.Series:
    suspended = _derive_bool_column(df, "is_suspended", default=False)
    if "trade_status" in df.columns:
        status = df["trade_status"].fillna("").astype(str).str.strip().str.lower()
        suspended = suspended | status.isin({"0", "停牌", "suspended", "halt", "halted"})
    if {"volume", "amount"}.issubset(df.columns):
        suspended = suspended | ((df["volume"].fillna(0) <= 0) & (df["amount"].fillna(0) <= 0))
    return suspended.astype(bool)


def _derive_board(df: pd.DataFrame) -> pd.Series:
    code = df["code"].astype(str).str.split(".").str[0].str.zfill(6)
    board = pd.Series("unknown", index=df.index, dtype="string")
    board.loc[code.str.startswith(("300", "301"))] = "chinext"
    board.loc[code.str.startswith(("688", "689"))] = "star"
    board.loc[code.str.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920"))] = "beijing"
    board.loc[code.str.startswith(("000", "001", "002", "003", "600", "601", "603", "605", "900"))] = "main"
    return board


def _derive_limit_rate(df: pd.DataFrame) -> pd.Series:
    rate = pd.Series(0.10, index=df.index, dtype="float64")
    rate.loc[df["board"].isin(["chinext", "star"])] = 0.20
    rate.loc[df["board"] == "beijing"] = 0.30
    rate.loc[df["is_st"]] = 0.05
    return rate


def _derive_limit_flag(df: pd.DataFrame, *, flag_col: str, direction: str) -> pd.Series:
    flag = _derive_bool_column(df, flag_col, default=False)
    if "pct_chg" in df.columns:
        pct = pd.to_numeric(df["pct_chg"], errors="coerce")
    elif "pre_close" in df.columns:
        pre_close = pd.to_numeric(df["pre_close"], errors="coerce")
        pct = (df["close"] / pre_close - 1.0) * 100.0
    else:
        return flag.astype(bool)

    # Small tolerance accounts for tick rounding. New listings are already rejected by age.
    threshold = df["limit_rate"] * 100 - 0.2
    if direction == "up":
        derived = pct >= threshold
    else:
        derived = pct <= -threshold
    return (flag | derived.fillna(False)).astype(bool)


def _derive_listing_days(df: pd.DataFrame) -> pd.Series:
    if "first_trade_date" in df.columns:
        first = pd.to_datetime(df["first_trade_date"], errors="coerce")
    elif "listing_days" in df.columns and df.groupby("code")["date"].transform("size").le(1).all():
        # Preserve precomputed snapshot values for the legacy snapshot API.
        return pd.to_numeric(df["listing_days"], errors="coerce").fillna(0).astype(int)
    else:
        first = df.groupby("code")["date"].transform("min")
    return (df["date"] - first).dt.days.add(1).fillna(0).astype(int)


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
    if not bool(row.get("st_metadata_available", True)):
        reasons.append("missing_st_metadata")
    elif not bool(row["pass_non_st"]):
        reasons.append("st_or_star_st")
    mapping = {
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

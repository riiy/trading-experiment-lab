from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from texperiment.setups.stock_rs_pullback_v1.rules import (
    is_entry_triggered,
    is_executable_row,
    passes_pullback_filter,
    passes_strength_filter,
)
from texperiment.setups.stock_rs_pullback_v1.schema import SIGNAL_OUTPUT_COLUMNS, Signal


@dataclass(frozen=True)
class StockRSPullbackSignalConfig:
    setup_id: str = "STOCK_RS_PULLBACK_v1"
    excess_return_min: float = 0.05
    require_close_above_ma20: bool = True
    require_ma20_above_ma60: bool = True
    require_20d_high_recent: bool = True
    drawdown_min: float = 0.03
    drawdown_max: float = 0.08
    volume_less_than_ma5: bool = True
    require_not_break_breakout_body_midpoint: bool = True
    require_first_pullback_in_strength_regime: bool = True
    trigger_window_days: int = 5
    entry_execution: str = "next_day_open"
    validation_start_date: str | None = None
    validation_end_date: str | None = None

    @classmethod
    def from_setup_config(cls, setup_config: dict[str, Any]) -> "StockRSPullbackSignalConfig":
        strength = setup_config.get("strength_filter", {})
        pullback = setup_config.get("pullback_filter", {})
        entry = setup_config.get("entry", {})
        validation_window = setup_config.get("validation_window", {})
        return cls(
            setup_id=str(setup_config.get("setup_id", "STOCK_RS_PULLBACK_v1")),
            excess_return_min=float(strength.get("excess_return_min", 0.05)),
            require_close_above_ma20=bool(strength.get("require_close_above_ma20", True)),
            require_ma20_above_ma60=bool(strength.get("require_ma20_above_ma60", True)),
            require_20d_high_recent=bool(strength.get("require_20d_high_recent", True)),
            drawdown_min=float(pullback.get("drawdown_min", 0.03)),
            drawdown_max=float(pullback.get("drawdown_max", 0.08)),
            volume_less_than_ma5=bool(pullback.get("volume_less_than_ma5", True)),
            require_not_break_breakout_body_midpoint=bool(
                pullback.get("require_not_break_breakout_body_midpoint", True)
            ),
            require_first_pullback_in_strength_regime=bool(
                pullback.get("require_first_pullback_in_strength_regime", True)
            ),
            trigger_window_days=int(entry.get("trigger_window_days", entry.get("reclaim_window_days", 5))),
            entry_execution=str(entry.get("execution", "next_day_open")),
            validation_start_date=_optional_date(validation_window.get("start_date")),
            validation_end_date=_optional_date(validation_window.get("end_date")),
        )


def annotate_signal_layer(
    indicators: pd.DataFrame,
    *,
    universe: pd.DataFrame | None = None,
    config: StockRSPullbackSignalConfig | None = None,
) -> pd.DataFrame:
    """Annotate indicator rows with strength, pullback and trigger primitives.

    The output is suitable for auditing. Signal generation itself remains
    stateful, because the setup requires a pullback after strength and then a
    later reclaim of the pullback-day high.
    """
    cfg = config or StockRSPullbackSignalConfig()
    out = _prepare_indicators(indicators)
    if universe is not None:
        out = _merge_universe(out, universe)

    out = _add_20d_high_recent(out)

    out["pass_strength_filter"] = _vectorized_strength_filter(out, cfg)
    out["pass_pullback_filter"] = _vectorized_pullback_filter(out, cfg)
    out["is_pullback_candidate"] = out["pass_strength_filter"] & out["pass_pullback_filter"]
    out["is_executable_row"] = _vectorized_executable_filter(out)
    return out.sort_values(["code", "date"]).reset_index(drop=True)


def generate_candidate_signals(
    df: pd.DataFrame,
    *,
    config: StockRSPullbackSignalConfig | None = None,
) -> list[Signal]:
    """Generate pullback candidate rows.

    This preserves the original simple API used by early tests. It does not
    require a later reclaim trigger. Formal validation should use
    ``generate_triggered_signals`` or ``build_stock_rs_pullback_signals``.
    """
    cfg = config or StockRSPullbackSignalConfig()
    annotated = annotate_signal_layer(df, config=cfg)
    signals: list[Signal] = []
    for _, row in annotated.loc[annotated["is_pullback_candidate"]].iterrows():
        r = row.to_dict()
        signal_date = _date_str(r["date"])
        signal_id = f"{cfg.setup_id}:{r['code']}:{signal_date}:candidate"
        signals.append(
            Signal(
                signal_id=signal_id,
                setup_id=cfg.setup_id,
                code=str(r["code"]),
                name=_optional_str(r.get("name")),
                signal_date=signal_date,
                pullback_date=signal_date,
                pullback_high=float(r["high"]),
                pullback_low=float(r["low"]),
                stop_price=float(r["low"]),
                status="candidate",
                entry_execution=cfg.entry_execution,
            )
        )
    return signals


def generate_triggered_signals(
    indicators: pd.DataFrame,
    *,
    universe: pd.DataFrame | None = None,
    config: StockRSPullbackSignalConfig | None = None,
    include_candidates: bool = False,
) -> list[dict[str, Any]]:
    """Generate triggered signals using a no-future state machine.

    For each stock:
    1. Enter a strength regime when ``pass_strength_filter`` is true.
    2. Record the first eligible pullback in that regime.
    3. Emit a triggered signal when a later close reclaims the pullback-day high.
    4. If no reclaim occurs within ``trigger_window_days``, expire the candidate.
    """
    cfg = config or StockRSPullbackSignalConfig()
    annotated = annotate_signal_layer(indicators, universe=universe, config=cfg)
    rows: list[dict[str, Any]] = []
    for _, group in annotated.groupby("code", sort=True):
        rows.extend(_generate_group_signals(
            group.sort_values("date").reset_index(drop=True),
            cfg,
            include_candidates=include_candidates,
        ))
    return rows


def _generate_group_signals(
    group: pd.DataFrame,
    cfg: StockRSPullbackSignalConfig,
    *,
    include_candidates: bool,
) -> list[dict[str, Any]]:
    strength = group["pass_strength_filter"].to_numpy(dtype=bool)
    executable = group["is_executable_row"].to_numpy(dtype=bool)
    candidates = (group["is_pullback_candidate"].to_numpy(dtype=bool) & executable)
    regime = pd.Series((~strength).cumsum())
    first_in_regime = candidates & (
        pd.Series(candidates).groupby(regime).cumsum().to_numpy() == 1
    )
    rows: list[dict[str, Any]] = []
    for position in first_in_regime.nonzero()[0]:
        candidate = group.iloc[position].to_dict()
        if include_candidates:
            rows.append(_candidate_to_output(candidate, cfg, status="candidate_pending_reclaim"))

        end = min(len(group), position + cfg.trigger_window_days + 1)
        future = group.iloc[position + 1:end]
        future_strength = strength[position + 1:end]
        first_strength_loss = (~future_strength).nonzero()[0]
        search_end = int(first_strength_loss[0]) if len(first_strength_loss) else len(future)
        search = future.iloc[:search_end]
        reclaim = (
            search["is_executable_row"].to_numpy(dtype=bool)
            & search["close"].gt(float(candidate["high"])).to_numpy()
        )
        trigger_positions = reclaim.nonzero()[0]
        if len(trigger_positions):
            trigger_position = int(trigger_positions[0])
            trigger = search.iloc[trigger_position].to_dict()
            rows.append(_trigger_to_output(
                candidate,
                trigger,
                cfg,
                days_to_trigger=trigger_position + 1,
            ))
        elif include_candidates:
            status = (
                "candidate_expired_strength_lost"
                if len(first_strength_loss)
                else "candidate_expired_no_reclaim"
            )
            rows.append(_candidate_to_output(candidate, cfg, status=status))
    return rows


def build_stock_rs_pullback_signals(
    indicators: pd.DataFrame,
    *,
    universe: pd.DataFrame | None = None,
    setup_config: dict[str, Any] | None = None,
    include_candidates: bool = False,
) -> pd.DataFrame:
    cfg = StockRSPullbackSignalConfig.from_setup_config(setup_config or {})
    rows = generate_triggered_signals(
        indicators,
        universe=universe,
        config=cfg,
        include_candidates=include_candidates,
    )
    if not rows:
        return pd.DataFrame(columns=SIGNAL_OUTPUT_COLUMNS)
    out = pd.DataFrame(rows)
    for col in SIGNAL_OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return _apply_validation_window(out[SIGNAL_OUTPUT_COLUMNS], cfg)


def validate_universe_coverage(indicators: pd.DataFrame, universe: pd.DataFrame) -> None:
    """Require date/code universe rows for every indicator row."""
    required = {"date", "code", "is_tradable_universe"}
    missing = sorted(required - set(universe.columns))
    if missing:
        raise ValueError(f"universe missing required columns: {missing}")
    if universe.empty:
        raise ValueError("universe is empty")

    indicator_keys = indicators[["date", "code"]].copy()
    indicator_keys["date"] = pd.to_datetime(indicator_keys["date"]).dt.normalize()
    indicator_keys["code"] = indicator_keys["code"].astype(str)
    universe_keys = universe[["date", "code"]].copy()
    universe_keys["date"] = pd.to_datetime(universe_keys["date"]).dt.normalize()
    universe_keys["code"] = universe_keys["code"].astype(str)
    universe_keys = universe_keys.drop_duplicates()
    covered = indicator_keys.merge(universe_keys, on=["date", "code"], how="left", indicator=True)
    missing_rows = covered.loc[covered["_merge"] == "left_only", ["date", "code"]]
    if not missing_rows.empty:
        sample = ", ".join(
            f"{row.code}@{row.date.date()}" for row in missing_rows.head(3).itertuples()
        )
        raise ValueError(
            f"universe does not cover {len(missing_rows)} indicator rows; sample: {sample}"
        )


def build_stock_rs_pullback_signals_from_parquet(
    indicator_path: str | Path,
    *,
    universe_path: str | Path | None = None,
    setup_config: dict[str, Any] | None = None,
    include_candidates: bool = False,
    require_universe: bool = False,
    batch_size: int = 250_000,
) -> pd.DataFrame:
    """Build signals from sorted Parquet files without loading full history."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if require_universe and universe_path is None:
        raise ValueError("require_universe requires universe_path")
    config = StockRSPullbackSignalConfig.from_setup_config(setup_config or {})
    indicator_file = pq.ParquetFile(indicator_path)
    universe_file = pq.ParquetFile(universe_path) if universe_path is not None else None
    universe_batches = universe_file.iter_batches(batch_size=batch_size) if universe_file else None
    carry_indicators: pd.DataFrame | None = None
    carry_universe: pd.DataFrame | None = None
    rows: list[dict[str, Any]] = []

    for indicator_batch in indicator_file.iter_batches(batch_size=batch_size):
        indicators = indicator_batch.to_pandas()
        universe = None
        if universe_batches is not None:
            try:
                universe = next(universe_batches).to_pandas()
            except StopIteration as exc:
                raise ValueError("universe has fewer rows than indicators") from exc
            _validate_stream_keys(indicators, universe)

        if carry_indicators is not None:
            indicators = pd.concat([carry_indicators, indicators], ignore_index=True)
            if universe is not None and carry_universe is not None:
                universe = pd.concat([carry_universe, universe], ignore_index=True)

        indicators = _prepare_indicators(indicators)
        if universe is not None:
            universe = _prepare_universe_stream_frame(universe)
        last_code = str(indicators.iloc[-1]["code"])
        complete_codes = [code for code in indicators["code"].astype(str).unique() if code != last_code]
        for code in complete_codes:
            ind_group = indicators.loc[indicators["code"].astype(str) == code].copy()
            uni_group = universe.loc[universe["code"].astype(str) == code].copy() if universe is not None else None
            rows.extend(generate_triggered_signals(
                ind_group,
                universe=uni_group,
                config=config,
                include_candidates=include_candidates,
            ))
        carry_indicators = indicators.loc[indicators["code"].astype(str) == last_code].copy()
        carry_universe = universe.loc[universe["code"].astype(str) == last_code].copy() if universe is not None else None

    if carry_indicators is None:
        raise ValueError("indicators is empty")
    last_code = str(carry_indicators.iloc[0]["code"])
    rows.extend(generate_triggered_signals(
        carry_indicators,
        universe=carry_universe,
        config=config,
        include_candidates=include_candidates,
    ))
    if universe_batches is not None:
        try:
            next(universe_batches)
        except StopIteration:
            pass
        else:
            raise ValueError("universe has more rows than indicators")
    if not rows:
        return pd.DataFrame(columns=SIGNAL_OUTPUT_COLUMNS)
    out = pd.DataFrame(rows)
    for col in SIGNAL_OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return _apply_validation_window(out[SIGNAL_OUTPUT_COLUMNS], config)


def _apply_validation_window(df: pd.DataFrame, config: StockRSPullbackSignalConfig) -> pd.DataFrame:
    out = df.copy()
    dates = pd.to_datetime(out["signal_date"], errors="coerce").dt.normalize()
    if config.validation_start_date is not None:
        out = out.loc[dates >= pd.Timestamp(config.validation_start_date)].copy()
        dates = dates.loc[out.index]
    if config.validation_end_date is not None:
        out = out.loc[dates <= pd.Timestamp(config.validation_end_date)].copy()
    return out.sort_values(["code", "signal_date", "status"]).reset_index(drop=True)


def _validate_stream_keys(indicators: pd.DataFrame, universe: pd.DataFrame) -> None:
    required = {"date", "code"}
    if not required.issubset(indicators.columns) or not required.issubset(universe.columns):
        raise ValueError("streaming inputs require date and code columns")
    left = indicators[["date", "code"]].copy()
    right = universe[["date", "code"]].copy()
    for frame in (left, right):
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame["code"] = frame["code"].astype(str)
    if len(left) != len(right) or not left.reset_index(drop=True).equals(right.reset_index(drop=True)):
        raise ValueError(
            "streaming universe must contain same date/code rows in same order as indicators; "
            "build it with --include-rejected"
        )


def _prepare_universe_stream_frame(df: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "code", "is_tradable_universe"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"universe missing required columns: {missing}")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["code"] = out["code"].astype(str)
    return out


def _vectorized_strength_filter(df: pd.DataFrame, cfg: StockRSPullbackSignalConfig) -> pd.Series:
    passed = pd.to_numeric(df["excess_ret20"], errors="coerce").gt(cfg.excess_return_min)
    if cfg.require_close_above_ma20:
        passed &= _flag_series(df, "close_above_ma20", df["close"] > df["ma20"])
    if cfg.require_ma20_above_ma60:
        passed &= _flag_series(df, "ma20_above_ma60", df["ma20"] > df["ma60"])
    if cfg.require_20d_high_recent:
        passed &= _flag_series(df, "made_20d_high_recent", pd.Series(False, index=df.index))
    return passed.fillna(False).astype(bool)


def _vectorized_pullback_filter(df: pd.DataFrame, cfg: StockRSPullbackSignalConfig) -> pd.Series:
    drawdown = pd.to_numeric(df["drawdown_from_10d_high"], errors="coerce")
    passed = drawdown.ge(cfg.drawdown_min) & drawdown.le(cfg.drawdown_max)
    if cfg.volume_less_than_ma5:
        passed &= _flag_series(df, "volume_below_ma5", df["volume"] < df["vol_ma5"])
    if cfg.require_close_above_ma20:
        passed &= _flag_series(df, "close_above_ma20", df["close"] > df["ma20"])
    if cfg.require_not_break_breakout_body_midpoint and "breakout_body_midpoint" in df.columns:
        midpoint = pd.to_numeric(df["breakout_body_midpoint"], errors="coerce")
        passed &= midpoint.isna() | df["close"].ge(midpoint)
    return passed.fillna(False).astype(bool)


def _vectorized_executable_filter(df: pd.DataFrame) -> pd.Series:
    passed = pd.Series(True, index=df.index, dtype=bool)
    if "is_tradable_universe" in df.columns:
        passed &= _flag_series(df, "is_tradable_universe", pd.Series(False, index=df.index))
    for column in ("is_suspended", "is_limit_up", "is_limit_down"):
        if column in df.columns:
            passed &= ~_flag_series(df, column, pd.Series(False, index=df.index))
    return passed


def _flag_series(df: pd.DataFrame, column: str, fallback: pd.Series) -> pd.Series:
    if column not in df.columns:
        return fallback.fillna(False).astype(bool)
    values = df[column]
    if values.dtype == bool:
        return values.fillna(False).astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    return normalized.isin({"1", "1.0", "true", "t", "yes", "y", "是"})


def write_signals(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        df.to_parquet(path, index=False)
    return path


def _prepare_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("indicators is empty")
    required = {"date", "code", "open", "high", "low", "close", "volume", "ma20", "ma60", "excess_ret20", "drawdown_from_10d_high", "vol_ma5"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"indicators missing required columns: {missing}")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["code"] = out["code"].astype(str)
    numeric_cols = ["open", "high", "low", "close", "volume", "ma20", "ma60", "excess_ret20", "drawdown_from_10d_high", "vol_ma5", "volume_ratio_to_ma5"]
    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.drop_duplicates(["date", "code"], keep="last")
    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    out["row_number"] = out.groupby("code").cumcount()
    return out


def _merge_universe(indicators: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    if universe.empty:
        return indicators.copy()
    required = {"date", "code", "is_tradable_universe"}
    missing = sorted(required - set(universe.columns))
    if missing:
        raise ValueError(f"universe missing required columns: {missing}")
    u = universe.copy()
    u["date"] = pd.to_datetime(u["date"]).dt.normalize()
    u["code"] = u["code"].astype(str)
    keep_cols = [
        c for c in [
            "date",
            "code",
            "is_tradable_universe",
            "is_st",
            "is_suspended",
            "is_limit_up",
            "is_limit_down",
            "avg_amount_20d",
            "one_lot_value",
            "reject_reasons",
        ] if c in u.columns
    ]
    u = u[keep_cols].drop_duplicates(["date", "code"], keep="last")
    # Prefer universe-derived executability fields over stale indicator fields.
    overlap = [c for c in keep_cols if c not in {"date", "code"} and c in indicators.columns]
    base = indicators.drop(columns=overlap, errors="ignore")
    return base.merge(u, on=["date", "code"], how="left")


def _add_20d_high_recent(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["code", "date"]).copy()
    if "made_20d_high_recent" in out.columns:
        out["made_20d_high_recent"] = out["made_20d_high_recent"].fillna(False).astype(bool)
        return out
    # A genuine 20-day high event must exceed or equal the prior 20-day high.
    prior_20d_high = (
        out.groupby("code", group_keys=False)["high"]
        .transform(lambda s: s.shift(1).rolling(window=20, min_periods=20).max())
    )
    out["is_20d_high_breakout"] = out["high"] >= prior_20d_high
    out["made_20d_high_recent"] = (
        out.groupby("code", group_keys=False)["is_20d_high_breakout"]
        .transform(lambda s: s.rolling(window=20, min_periods=1).max())
        .fillna(False)
        .astype(bool)
    )
    return out


def _candidate_to_output(candidate: dict[str, Any], cfg: StockRSPullbackSignalConfig, *, status: str) -> dict[str, Any]:
    pullback_date = _date_str(candidate["date"])
    signal_id = f"{cfg.setup_id}:{candidate['code']}:{pullback_date}:{status}"
    return {
        "signal_id": signal_id,
        "setup_id": cfg.setup_id,
        "code": str(candidate["code"]),
        "name": _optional_str(candidate.get("name")),
        "signal_date": pullback_date,
        "pullback_date": pullback_date,
        "trigger_date": pd.NA,
        "status": status,
        "entry_execution": cfg.entry_execution,
        "pullback_high": float(candidate["high"]),
        "pullback_low": float(candidate["low"]),
        "stop_price": float(candidate["low"]),
        "trigger_close": pd.NA,
        "days_to_trigger": pd.NA,
        "excess_ret20_at_pullback": _safe_float(candidate.get("excess_ret20")),
        "drawdown_from_10d_high_at_pullback": _safe_float(candidate.get("drawdown_from_10d_high")),
        "volume_ratio_to_ma5_at_pullback": _safe_float(candidate.get("volume_ratio_to_ma5")),
        "is_tradable_universe_at_pullback": _optional_bool(candidate.get("is_tradable_universe")),
        "is_tradable_universe_at_trigger": pd.NA,
        "invalid_reason": None if status == "candidate_pending_reclaim" else status,
    }


def _trigger_to_output(candidate: dict[str, Any], trigger: dict[str, Any], cfg: StockRSPullbackSignalConfig, *, days_to_trigger: int) -> dict[str, Any]:
    pullback_date = _date_str(candidate["date"])
    trigger_date = _date_str(trigger["date"])
    signal_id = f"{cfg.setup_id}:{trigger['code']}:{pullback_date}:{trigger_date}:triggered"
    return {
        "signal_id": signal_id,
        "setup_id": cfg.setup_id,
        "code": str(trigger["code"]),
        "name": _optional_str(trigger.get("name", candidate.get("name"))),
        "signal_date": trigger_date,
        "pullback_date": pullback_date,
        "trigger_date": trigger_date,
        "status": "triggered_entry_next_open",
        "entry_execution": cfg.entry_execution,
        "pullback_high": float(candidate["high"]),
        "pullback_low": float(candidate["low"]),
        "stop_price": float(candidate["low"]),
        "trigger_close": _safe_float(trigger.get("close")),
        "days_to_trigger": int(days_to_trigger),
        "excess_ret20_at_pullback": _safe_float(candidate.get("excess_ret20")),
        "drawdown_from_10d_high_at_pullback": _safe_float(candidate.get("drawdown_from_10d_high")),
        "volume_ratio_to_ma5_at_pullback": _safe_float(candidate.get("volume_ratio_to_ma5")),
        "is_tradable_universe_at_pullback": _optional_bool(candidate.get("is_tradable_universe")),
        "is_tradable_universe_at_trigger": _optional_bool(trigger.get("is_tradable_universe")),
        "invalid_reason": None,
    }


def _trading_day_distance(start_row_number: Any, end_row_number: Any) -> int:
    return int(end_row_number) - int(start_row_number)


def _date_str(value: Any) -> str:
    return str(pd.Timestamp(value).date())


def _optional_date(value: Any) -> str | None:
    if value is None or str(value).strip() in {"", "None", "none"}:
        return None
    return str(pd.Timestamp(value).normalize().date())


def _optional_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    try:
        number = float(value)
        if number in {0.0, 1.0}:
            return bool(number)
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {"1", "1.0", "true", "t", "yes", "y", "是"}

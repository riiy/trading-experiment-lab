from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pandas as pd

AUDIT_PLAN_VERSION = "AUDIT_STOCK_RS_PULLBACK_v1_PLAN_v1"
AUDIT_RANDOM_SEED = 20260717

_RANDOM_QUOTAS = {
    "stop_loss": 12,
    "target_2r": 10,
    "time_stop_no_upside_progress": 10,
    "max_holding_exit": 8,
    "invalid_trade": 5,
}

_CATEGORY_ORDER = [
    "extreme_gain",
    "extreme_loss",
    "stop_loss",
    "target_2r",
    "time_stop_no_upside_progress",
    "max_holding_exit",
    "invalid_trade",
]


def canonical_trade_hash(row: pd.Series | dict[str, Any]) -> str:
    payload = {str(key): _canonical_value(value) for key, value in dict(row).items() if key != "source_trade_hash"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def select_audit_sample(
    trades: pd.DataFrame,
    *,
    random_seed: int = AUDIT_RANDOM_SEED,
) -> pd.DataFrame:
    """Select exactly 50 mutually exclusive rows using frozen deterministic rules."""
    required = {"trade_id", "signal_id", "status", "net_return", "exit_date", "code", "exit_reason"}
    missing = sorted(required - set(trades.columns))
    if missing:
        raise ValueError(f"trades missing audit sample columns: {missing}")

    frame = trades.copy().reset_index(drop=True)
    frame["source_trade_row"] = frame.index.astype(int)
    frame["source_trade_hash"] = frame.apply(canonical_trade_hash, axis=1)
    frame["_net_return"] = pd.to_numeric(frame["net_return"], errors="coerce")
    frame["_exit_date"] = pd.to_datetime(frame["exit_date"], errors="coerce")
    frame["_code"] = frame["code"].astype(str)
    frame["_trade_id"] = frame["trade_id"].astype(str)

    valid = frame.loc[(frame["status"] == "valid_trade") & frame["_net_return"].notna()].copy()
    gains = _stable_extreme_sort(valid, descending=True).head(3).copy()
    losses_pool = valid.loc[~valid.index.isin(gains.index)]
    losses = _stable_extreme_sort(losses_pool, descending=False).head(2).copy()
    if len(gains) != 3 or len(losses) != 2:
        raise ValueError("insufficient valid trades for five extreme samples")

    selected: list[pd.DataFrame] = [
        _label(gains, "extreme_gain", "stable_extreme_sort", random_seed),
        _label(losses, "extreme_loss", "stable_extreme_sort", random_seed),
    ]
    used = set(gains.index) | set(losses.index)

    for category, quota in _RANDOM_QUOTAS.items():
        if category == "invalid_trade":
            pool = frame.loc[frame["status"] != "valid_trade"].copy()
        else:
            pool = frame.loc[
                (frame["status"] == "valid_trade")
                & (frame["exit_reason"] == category)
                & ~frame.index.isin(used)
            ].copy()
        if len(pool) < quota:
            raise ValueError(f"insufficient rows for {category}: need {quota}, found {len(pool)}")
        pool["_random_rank"] = pool["source_trade_hash"].map(
            lambda value: hashlib.sha256(f"{random_seed}|{category}|{value}".encode()).hexdigest()
        )
        sample = pool.sort_values(
            ["_random_rank", "_exit_date", "_code", "_trade_id", "source_trade_row"],
            kind="stable",
        ).head(quota)
        used.update(sample.index)
        selected.append(_label(sample, category, "sha256_seeded_rank", random_seed))

    output = pd.concat(selected, ignore_index=True)
    if len(output) != 50 or output["source_trade_row"].nunique() != 50:
        raise AssertionError("audit sample must contain exactly 50 mutually exclusive rows")
    output["_category_order"] = output["audit_category"].map({name: i for i, name in enumerate(_CATEGORY_ORDER)})
    output = output.sort_values(["_category_order", "selection_rank"], kind="stable")
    return output.drop(columns=[column for column in output.columns if column.startswith("_")]).reset_index(drop=True)


def _stable_extreme_sort(frame: pd.DataFrame, *, descending: bool) -> pd.DataFrame:
    return frame.sort_values(
        ["_net_return", "_exit_date", "_code", "_trade_id", "source_trade_row"],
        ascending=[not descending, True, True, True, True],
        kind="stable",
    )


def _label(frame: pd.DataFrame, category: str, method: str, seed: int) -> pd.DataFrame:
    out = frame.copy()
    out["audit_category"] = category
    out["selection_method"] = method
    out["selection_rank"] = range(1, len(out) + 1)
    out["random_seed"] = seed
    return out


def _canonical_value(value: Any) -> Any:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return format(value, ".15g")
    if isinstance(value, (int, bool, str)):
        return value
    return str(value)

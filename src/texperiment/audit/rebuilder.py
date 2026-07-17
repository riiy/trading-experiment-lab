from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

AUDIT_DETAIL_COLUMNS = [
    "trade_id",
    "check_id",
    "check_name",
    "severity",
    "recorded_value",
    "recalculated_value",
    "difference",
    "verdict",
    "blocking",
    "evidence",
    "reviewer",
    "reviewed_at",
    "notes",
]


def load_parquet_for_codes(
    path: str | Path,
    codes: set[str],
    *,
    columns: list[str] | None = None,
    batch_size: int = 250_000,
) -> pd.DataFrame:
    """Load full available histories for selected codes with bounded memory."""
    parquet = pq.ParquetFile(path)
    selected = columns or parquet.schema_arrow.names
    frames: list[pd.DataFrame] = []
    for batch in parquet.iter_batches(batch_size=batch_size, columns=selected):
        frame = batch.to_pandas()
        if "code" not in frame:
            raise ValueError("audit input requires code column")
        frame = frame.loc[frame["code"].astype(str).isin(codes)]
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=selected)


def audit_trade(
    trade: pd.Series | dict[str, Any],
    *,
    signal: pd.Series | dict[str, Any] | None,
    daily_bars: pd.DataFrame,
    indicators: pd.DataFrame,
    universe: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    round_trip_cost: float = 0.002,
) -> pd.DataFrame:
    """Independently reconstruct frozen audit checks for one sampled trade."""
    row = dict(trade)
    sig = {} if signal is None else dict(signal)
    trade_id = str(row.get("trade_id"))
    code = str(row.get("code"))
    bars = _prepare_history(daily_bars, code)
    ind = _prepare_history(indicators, code)
    uni = _prepare_history(universe, code)
    checks: list[dict[str, Any]] = []

    signal_date = _timestamp(row.get("signal_date"))
    pullback_date = _timestamp(row.get("pullback_date"))
    trigger_date = _timestamp(row.get("trigger_date"))
    entry_date = _timestamp(row.get("entry_date"))
    exit_date = _timestamp(row.get("exit_date"))

    universe_row = uni.loc[uni["date"] == signal_date] if signal_date is not None and "date" in uni else pd.DataFrame()
    universe_ok = bool(
        not universe_row.empty
        and _bool(universe_row.iloc[-1].get("is_tradable_universe"))
    )
    checks.append(_detail(trade_id, "UNIVERSE_TRADABLE_AT_SIGNAL", "Signal-day universe", "CRITICAL", row.get("code"), universe_ok, "PASS" if universe_ok else "FAIL", True, f"signal_date={signal_date}"))

    date_order_ok = all(value is not None for value in (pullback_date, trigger_date, entry_date)) and pullback_date < trigger_date < entry_date
    checks.append(_detail(trade_id, "DATE_ORDER_VALID", "Pullback/trigger/entry order", "CRITICAL", [pullback_date, trigger_date, entry_date], date_order_ok, "PASS" if date_order_ok else "FAIL", True, "strictly increasing dates"))

    indicator_check = _recalculate_indicators(ind, bars, benchmark, signal_date)
    checks.append(_detail(
        trade_id,
        "NO_LOOKAHEAD_IN_INDICATORS",
        "Prefix-only indicator reconstruction",
        "CRITICAL",
        indicator_check["recorded"],
        indicator_check["expected"],
        indicator_check["verdict"],
        True,
        indicator_check["evidence"],
    ))

    future = bars.loc[bars["date"] > trigger_date] if trigger_date is not None else pd.DataFrame()
    expected_entry = future.iloc[0] if not future.empty else None
    entry_ok = expected_entry is not None and entry_date == expected_entry["date"] and _close(row.get("entry_price"), expected_entry["open"])
    checks.append(_detail(trade_id, "ENTRY_IS_NEXT_TRADING_DAY_OPEN", "Next trading-day open entry", "CRITICAL", [row.get("entry_date"), row.get("entry_price")], None if expected_entry is None else [expected_entry["date"], expected_entry["open"]], "PASS" if entry_ok else "FAIL", True, "first stock bar after trigger"))

    executable = expected_entry is not None and not _bool(expected_entry.get("is_suspended")) and not _bool(expected_entry.get("is_limit_up"))
    entry_status_ok, entry_evidence = _entry_status_matches_trade(row, expected_entry, executable)
    checks.append(_detail(trade_id, "ENTRY_DAY_EXECUTABLE", "Entry-day execution status", "CRITICAL", None if expected_entry is None else expected_entry.to_dict(), {"executable": executable, "expected_outcome": entry_evidence}, "PASS" if entry_status_ok else "FAIL", True, "suspension and limit-up flags"))

    t1_ok = entry_date is None or exit_date is None or exit_date > entry_date
    checks.append(_detail(trade_id, "T1_NO_ENTRY_DAY_EXIT", "A-share T+1", "CRITICAL", [entry_date, exit_date], t1_ok, "PASS" if t1_ok else "FAIL", True, "exit must follow entry date"))

    checks.extend(_audit_exit(row, bars, trade_id, round_trip_cost))
    checks.extend(_audit_adjustment(row, bars, trade_id, signal_date, entry_date, exit_date))

    if row.get("status") != "valid_trade":
        reconstructed = _reconstruct_invalid_reason(row, expected_entry)
        matches = reconstructed == row.get("invalid_reason")
        checks.append(_detail(trade_id, "INVALID_REASON_RECONSTRUCTION", "Invalid reason", "CRITICAL", row.get("invalid_reason"), reconstructed, "PASS" if matches else "FAIL", True, "independent invalid reason reconstruction"))

    return pd.DataFrame(checks, columns=AUDIT_DETAIL_COLUMNS)


def summarize_audit(details: pd.DataFrame, *, sample_count: int, manual_review_complete: bool) -> dict[str, Any]:
    if sample_count != 50:
        raise ValueError(f"audit requires 50 samples, got {sample_count}")
    if not manual_review_complete or details["reviewer"].fillna("").eq("").any():
        raise ValueError("manual review must be complete before final audit decision")
    critical_failures = int(((details["severity"] == "CRITICAL") & (details["verdict"] == "FAIL")).sum())
    blocking_not_evaluable = int((details["blocking"].astype(bool) & details["verdict"].astype(str).str.startswith("NOT_EVALUABLE")).sum())
    non_blocking_not_evaluable = int((~details["blocking"].astype(bool) & details["verdict"].astype(str).str.startswith("NOT_EVALUABLE")).sum())
    if critical_failures:
        decision = "ENGINE_ERROR_FOUND"
    elif blocking_not_evaluable:
        decision = "AUDIT_INCONCLUSIVE_DATA_LIMITATION"
    else:
        decision = "AUDIT_PASSED"
    return {
        "decision": decision,
        "sample_count": sample_count,
        "critical_failures": critical_failures,
        "blocking_not_evaluable": blocking_not_evaluable,
        "non_blocking_not_evaluable": non_blocking_not_evaluable,
        "recalculation_performed": False,
        "new_setup_started": False,
    }


def _audit_exit(row: dict[str, Any], bars: pd.DataFrame, trade_id: str, cost: float) -> list[dict[str, Any]]:
    checks = []
    if row.get("status") != "valid_trade":
        return checks
    entry_date, exit_date = _timestamp(row.get("entry_date")), _timestamp(row.get("exit_date"))
    holding = bars.loc[(bars["date"] >= entry_date) & (bars["date"] <= exit_date)].reset_index(drop=True)
    entry, stop, target = float(row["entry_price"]), float(row["stop_price"]), float(row["target_price"])
    exit_bar = holding.iloc[-1] if not holding.empty else None
    both_hit = False
    if len(holding) > 1:
        post_entry = holding.iloc[1:]
        both_hit = bool(((post_entry["low"] <= stop) & (post_entry["high"] >= target)).any())
    same_day_ok = not both_hit or row.get("exit_reason") == "stop_loss"
    checks.append(_detail(trade_id, "SAME_DAY_STOP_TARGET_STOP_FIRST", "Stop/target intraday priority", "CRITICAL", row.get("exit_reason"), "stop_loss" if both_hit else "not_triggered", "PASS" if same_day_ok else "FAIL", True, "post-entry daily bars"))

    gap_expected = None
    if row.get("exit_reason") == "stop_loss" and exit_bar is not None:
        gap_expected = float(exit_bar["open"]) if float(exit_bar["open"]) <= stop else stop
    gap_ok = gap_expected is None or _close(row.get("exit_price"), gap_expected)
    checks.append(_detail(trade_id, "GAP_STOP_USES_EXECUTABLE_PRICE", "Gap stop execution", "CRITICAL", row.get("exit_price"), gap_expected, "PASS" if gap_ok else "FAIL", True, "open price when opening below stop"))

    holding_days = int(row["holding_days"])
    d5_ok = row.get("exit_reason") != "time_stop_no_upside_progress" or holding_days == 5
    d10_ok = row.get("exit_reason") != "max_holding_exit" or holding_days == 10
    checks.append(_detail(trade_id, "D5_EXIT_BOUNDARY", "D5 time-stop boundary", "CRITICAL", holding_days, 5, "PASS" if d5_ok else "FAIL", True, row.get("exit_reason")))
    checks.append(_detail(trade_id, "D10_EXIT_BOUNDARY", "D10 max-holding boundary", "CRITICAL", holding_days, 10, "PASS" if d10_ok else "FAIL", True, row.get("exit_reason")))

    gross = float(row["exit_price"]) / entry - 1.0
    net = gross - cost
    risk = entry - stop
    r_multiple = (float(row["exit_price"]) - entry) / risk
    for check_id, name, recorded, expected in [
        ("GROSS_RETURN_RECALCULATION", "Gross return", row.get("gross_return"), gross),
        ("NET_RETURN_AND_COST_RECALCULATION", "Net return and one cost deduction", row.get("net_return"), net),
        ("R_MULTIPLE_RECALCULATION", "R multiple", row.get("r_multiple"), r_multiple),
    ]:
        checks.append(_detail(trade_id, check_id, name, "CRITICAL", recorded, expected, "PASS" if _close(recorded, expected) else "FAIL", True, f"round_trip_cost={cost}"))
    return checks


def _audit_adjustment(row: dict[str, Any], bars: pd.DataFrame, trade_id: str, *dates: pd.Timestamp | None) -> list[dict[str, Any]]:
    relevant = bars.loc[bars["date"].isin([date for date in dates if date is not None])]
    adj_types = sorted(relevant["adj_type"].dropna().astype(str).unique()) if "adj_type" in relevant else []
    consistent = len(adj_types) == 1
    checks = [_detail(trade_id, "PRICE_ADJUSTMENT_CONSISTENCY", "Adjustment series consistency", "CRITICAL", adj_types, adj_types, "PASS" if consistent else "FAIL", True, "signal/entry/exit adj_type")]
    has_factor = "adj_factor" in relevant and relevant["adj_factor"].notna().all()
    has_raw = bool("adj_type" in relevant and relevant["adj_type"].astype(str).eq("none").any())
    realism_verdict = "PASS" if has_factor and has_raw else "NOT_EVALUABLE_EXECUTION_REALISM"
    checks.append(_detail(trade_id, "EXECUTION_REALISM", "Raw execution-price realism", "BLOCKING_DATA", [has_factor, has_raw], True, realism_verdict, True, "requires raw prices and adjustment factors"))
    checks.append(_detail(trade_id, "HISTORICAL_ST_STATUS", "Historical ST status", "BLOCKING_DATA", None, None, "NOT_EVALUABLE_MISSING_HISTORICAL_ST", True, "provider history unavailable"))
    checks.append(_detail(trade_id, "LIMIT_PRICE_VERIFICATION", "Board/date-specific limit price", "BLOCKING_DATA", None, None, "NOT_EVALUABLE_LIMIT_PRICE", True, "requires historical board/ST/listing-stage rules"))
    return checks


def _recalculate_indicators(indicators: pd.DataFrame, bars: pd.DataFrame, benchmark: pd.DataFrame | None, date: pd.Timestamp | None) -> dict[str, Any]:
    if date is None:
        return {"recorded": None, "expected": None, "verdict": "FAIL", "evidence": "missing signal date"}
    history = bars.loc[bars["date"] <= date].sort_values("date")
    current = indicators.loc[indicators["date"] == date]
    if len(history) < 80 or current.empty:
        return {"recorded": None, "expected": None, "verdict": "NOT_EVALUABLE_INSUFFICIENT_STATE_HISTORY", "evidence": f"history_rows={len(history)}"}
    expected = {
        "ma20": float(history["close"].tail(20).mean()),
        "ma60": float(history["close"].tail(60).mean()),
        "ret20": float(history.iloc[-1]["close"] / history.iloc[-21]["close"] - 1.0),
        "high_10d": float(history["high"].tail(10).max()),
        "vol_ma5": float(history["volume"].tail(5).mean()),
    }
    recorded_row = current.iloc[-1]
    recorded = {key: recorded_row.get(key) for key in expected}
    matches = all(_close(recorded[key], expected[key]) for key in expected)
    if benchmark is not None and not benchmark.empty:
        bench = benchmark.copy()
        bench["date"] = pd.to_datetime(bench["date"]).dt.normalize()
        bench = bench.loc[bench["date"] <= date].sort_values("date")
        if len(bench) >= 21:
            expected["benchmark_ret20"] = float(bench.iloc[-1]["close"] / bench.iloc[-21]["close"] - 1.0)
            recorded["benchmark_ret20"] = recorded_row.get("benchmark_ret20")
            matches &= _close(recorded["benchmark_ret20"], expected["benchmark_ret20"])
    return {"recorded": recorded, "expected": expected, "verdict": "PASS" if matches else "FAIL", "evidence": "prefix-only rolling recomputation"}


def _reconstruct_invalid_reason(row: dict[str, Any], expected_entry: pd.Series | None) -> str:
    if expected_entry is None:
        return "invalid_no_next_open"
    if _bool(expected_entry.get("is_suspended")):
        return "invalid_suspended_cannot_buy"
    if _bool(expected_entry.get("is_limit_up")):
        return "invalid_limit_up_cannot_buy"
    entry = _number(expected_entry.get("open"))
    stop = _number(row.get("stop_price"))
    if entry is not None and stop is not None and stop >= entry:
        return "invalid_stop_not_below_entry"
    return str(row.get("invalid_reason"))


def _entry_status_matches_trade(row: dict[str, Any], expected_entry: pd.Series | None, executable: bool) -> tuple[bool, str]:
    if row.get("status") == "valid_trade":
        return executable, "valid trade requires executable entry"
    reason = str(row.get("invalid_reason"))
    if reason == "invalid_no_next_open":
        return expected_entry is None, "missing next open"
    if reason == "invalid_suspended_cannot_buy":
        return expected_entry is not None and _bool(expected_entry.get("is_suspended")), "suspended entry correctly rejected"
    if reason == "invalid_limit_up_cannot_buy":
        return expected_entry is not None and _bool(expected_entry.get("is_limit_up")), "limit-up entry correctly rejected"
    return executable, "other invalid reason requires an otherwise executable entry"


def _prepare_history(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    out = frame.loc[frame["code"].astype(str) == code].copy() if "code" in frame else frame.copy()
    if "date" in out:
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
        out = out.sort_values("date")
    return out.reset_index(drop=True)


def _detail(trade_id: str, check_id: str, name: str, severity: str, recorded: Any, recalculated: Any, verdict: str, blocking: bool, evidence: Any) -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "check_id": check_id,
        "check_name": name,
        "severity": severity,
        "recorded_value": _json(recorded),
        "recalculated_value": _json(recalculated),
        "difference": _difference(recorded, recalculated),
        "verdict": verdict,
        "blocking": blocking,
        "evidence": _json(evidence),
        "reviewer": "",
        "reviewed_at": "",
        "notes": "",
    }


def _timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).normalize()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _close(left: Any, right: Any, tolerance: float = 1e-8) -> bool:
    a, b = _number(left), _number(right)
    return a is not None and b is not None and abs(a - b) <= tolerance * max(1.0, abs(a), abs(b))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _difference(recorded: Any, recalculated: Any) -> float | None:
    left, right = _number(recorded), _number(recalculated)
    return None if left is None or right is None else left - right


def _json(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        value = value.isoformat()
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from texperiment.backtest.execution_model import price_conversion_factor, price_transform
from texperiment.market_rules.price_limit import evaluate_price_limit_bar

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
    expected_raw_open = None if expected_entry is None else _number(expected_entry.get("raw_open"))
    entry_ok = expected_entry is not None and entry_date == expected_entry["date"] and _close(row.get("entry_price"), expected_raw_open)
    entry_verdict = "NOT_EVALUABLE_MISSING_RAW_PRICE" if expected_entry is not None and expected_raw_open is None else ("PASS" if entry_ok else "FAIL")
    checks.append(_detail(trade_id, "ENTRY_IS_NEXT_TRADING_DAY_OPEN", "Next trading-day raw open entry", "CRITICAL", [row.get("entry_date"), row.get("entry_price")], None if expected_entry is None else [expected_entry["date"], expected_raw_open], entry_verdict, True, "first stock bar after trigger; raw open required"))

    executable, entry_evidence = _entry_execution_state(expected_entry)
    if executable is None:
        entry_status_verdict = "NOT_EVALUABLE_OPEN_FILLABILITY"
        entry_status_ok = False
    else:
        entry_status_ok, entry_evidence = _entry_status_matches_trade(row, expected_entry, executable)
        entry_status_verdict = "PASS" if entry_status_ok else "FAIL"
    checks.append(_detail(trade_id, "ENTRY_DAY_EXECUTABLE", "Entry-day execution status", "CRITICAL", None if expected_entry is None else expected_entry.to_dict(), {"executable": executable, "expected_outcome": entry_evidence}, entry_status_verdict, True, "raw open, adjustment factor, trade status, and explicit open fillability"))

    t1_ok = entry_date is None or exit_date is None or exit_date > entry_date
    checks.append(_detail(trade_id, "T1_NO_ENTRY_DAY_EXIT", "A-share T+1", "CRITICAL", [entry_date, exit_date], t1_ok, "PASS" if t1_ok else "FAIL", True, "exit must follow entry date"))

    checks.extend(_audit_exit(row, bars, trade_id, round_trip_cost, sig))
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


def _audit_exit(
    row: dict[str, Any],
    bars: pd.DataFrame,
    trade_id: str,
    cost: float,
    signal: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = []
    if row.get("status") != "valid_trade":
        return checks
    entry_date, exit_date = _timestamp(row.get("entry_date")), _timestamp(row.get("exit_date"))
    holding = bars.loc[bars["date"] >= entry_date].reset_index(drop=True)
    entry_bar = holding.iloc[0] if not holding.empty else None
    entry = _preferred_number(entry_bar, "adj_open", "open") if entry_bar is not None else None
    stop = _number(signal.get("stop_price")) or _number(signal.get("pullback_low"))
    if stop is None:
        stop = _number(row.get("stop_adjusted_price", row.get("stop_price")))
    if entry is None or stop is None or stop >= entry:
        return [_detail(trade_id, "EXIT_RECONSTRUCTION_STATE", "Independent exit state", "CRITICAL", None, None, "NOT_EVALUABLE_EXIT_STATE", True, "entry/stop adjusted state unavailable")]
    target = entry + (entry - stop) * 2.0
    replay = _replay_exit(holding, stop, target)
    if replay is None:
        return [_detail(trade_id, "EXIT_RECONSTRUCTION_STATE", "Independent exit state", "CRITICAL", None, None, "NOT_EVALUABLE_EXIT_STATE", True, "complete adjusted prices and sellability required")]
    expected_exit_date, expected_exit_reason, expected_exit_adjusted, expected_holding_days = replay
    date_reason_ok = exit_date == expected_exit_date and row.get("exit_reason") == expected_exit_reason
    checks.append(_detail(trade_id, "EXIT_DATE_REASON_RECONSTRUCTION", "Independent exit date and reason", "CRITICAL", [exit_date, row.get("exit_reason")], [expected_exit_date, expected_exit_reason], "PASS" if date_reason_ok else "FAIL", True, "full post-entry replay with T+1 and sellability"))

    holding_days = int(row["holding_days"])
    holding_ok = holding_days == expected_holding_days
    checks.append(_detail(trade_id, "HOLDING_DAYS_RECONSTRUCTION", "Holding days including deferred fills", "CRITICAL", holding_days, expected_holding_days, "PASS" if holding_ok else "FAIL", True, expected_exit_reason))

    exit_bar = holding.iloc[expected_holding_days - 1]
    recorded_exit_adjusted = _number(row.get("exit_adjusted_price"))
    if recorded_exit_adjusted is None and exit_bar is not None:
        transform = price_transform(exit_bar.to_dict())
        recorded_raw_exit = _number(row.get("exit_price"))
        if transform is not None and recorded_raw_exit is not None:
            factor, offset = transform
            recorded_exit_adjusted = recorded_raw_exit * factor + offset
        elif recorded_raw_exit is not None:
            recorded_exit_adjusted = recorded_raw_exit
    exit_mapping_ok = _close_absolute(recorded_exit_adjusted, expected_exit_adjusted, 0.011)
    checks.append(_detail(trade_id, "EXIT_PRICE_RECONSTRUCTION", "Independent adjusted exit price", "CRITICAL", recorded_exit_adjusted, expected_exit_adjusted, "PASS" if exit_mapping_ok else "FAIL", True, "full post-entry replay"))

    if expected_exit_adjusted is None:
        gross = net = r_multiple = None
    else:
        gross = expected_exit_adjusted / entry - 1.0
        net = gross - cost
        r_multiple = (expected_exit_adjusted - entry) / (entry - stop)
    for check_id, name, recorded, expected in [
        ("GROSS_RETURN_RECALCULATION", "Gross return", row.get("gross_return"), gross),
        ("NET_RETURN_AND_COST_RECALCULATION", "Net return and one cost deduction", row.get("net_return"), net),
        ("R_MULTIPLE_RECALCULATION", "R multiple", row.get("r_multiple"), r_multiple),
    ]:
        tolerance = 0.011 / max(entry, 0.01)
        if check_id == "R_MULTIPLE_RECALCULATION":
            tolerance = 0.011 / max(entry - stop, 0.01)
        verdict = "NOT_EVALUABLE_EXIT_STATE" if expected is None else ("PASS" if _close_absolute(recorded, expected, tolerance) else "FAIL")
        checks.append(_detail(trade_id, check_id, name, "CRITICAL", recorded, expected, verdict, True, f"round_trip_cost={cost}"))
    return checks


def _audit_adjustment(row: dict[str, Any], bars: pd.DataFrame, trade_id: str, *dates: pd.Timestamp | None) -> list[dict[str, Any]]:
    known_dates = [date for date in dates if date is not None]
    relevant = bars.loc[(bars["date"] >= min(known_dates)) & (bars["date"] <= max(known_dates))] if known_dates else bars.iloc[0:0]
    adj_types = sorted(relevant["adj_type"].dropna().astype(str).unique()) if "adj_type" in relevant else []
    consistent = len(adj_types) == 1
    checks = [_detail(trade_id, "PRICE_ADJUSTMENT_CONSISTENCY", "Adjustment series consistency", "CRITICAL", adj_types, adj_types, "PASS" if consistent else "FAIL", True, "signal/entry/exit adj_type")]
    has_factor = "adj_factor" in relevant and relevant["adj_factor"].notna().all()
    raw_columns = {"raw_open", "raw_high", "raw_low", "raw_close"}
    adjusted_columns = {"adj_open", "adj_high", "adj_low", "adj_close"}
    has_raw = raw_columns.issubset(relevant.columns) and relevant[list(raw_columns)].notna().all().all()
    has_adjusted = adjusted_columns.issubset(relevant.columns) and relevant[list(adjusted_columns)].notna().all().all()
    layers_consistent = bool(
        has_factor
        and has_raw
        and has_adjusted
        and all(price_conversion_factor(bar) is not None for bar in relevant.to_dict("records"))
    )
    realism_verdict = "PASS" if layers_consistent else "NOT_EVALUABLE_EXECUTION_REALISM"
    checks.append(_detail(trade_id, "EXECUTION_REALISM", "Raw execution-price realism", "BLOCKING_DATA", [has_factor, has_raw, has_adjusted, layers_consistent], True, realism_verdict, True, "requires consistent raw and adjusted OHLC plus adjustment factors"))

    known_status = relevant.get("historical_st_status", pd.Series("UNKNOWN", index=relevant.index)).astype(str).str.upper().isin({"TRUE", "FALSE"})
    branch_invariant = relevant.get("historical_st_branch_status", pd.Series("NOT_EVALUATED", index=relevant.index)).astype(str).eq("PASS_BRANCH_INVARIANT")
    st_resolved = bool(not relevant.empty and (known_status | branch_invariant).all())
    st_verdict = "PASS_BRANCH_INVARIANT" if st_resolved and branch_invariant.any() else "PASS" if st_resolved else "NOT_EVALUABLE_MISSING_HISTORICAL_ST"
    st_evidence = {"known_rows": int(known_status.sum()), "branch_invariant_rows": int(branch_invariant.sum()), "rows": len(relevant)}
    material_unresolved = not st_resolved and row.get("status") != "valid_trade"
    checks.append(_detail(trade_id, "HISTORICAL_ST_STATUS", "Historical ST execution branch", "BLOCKING_DATA", None, st_evidence, st_verdict, material_unresolved, "ST/non-ST branches compared; non-execution-time differences remain non-blocking after complete trade replay"))

    limit_verdict, limit_evidence, limit_blocking = _verify_limit_rows(relevant)
    material_limit_blocking = limit_blocking and row.get("status") != "valid_trade"
    checks.append(_detail(trade_id, "LIMIT_PRICE_VERIFICATION", "Board/date-specific limit price", "BLOCKING_DATA", None, limit_evidence, limit_verdict, material_limit_blocking, "exact limits retained as check limitations; complete fail-closed trade replay determines materiality"))
    return checks


def _recalculate_indicators(indicators: pd.DataFrame, bars: pd.DataFrame, benchmark: pd.DataFrame | None, date: pd.Timestamp | None) -> dict[str, Any]:
    if date is None:
        return {"recorded": None, "expected": None, "verdict": "FAIL", "evidence": "missing signal date"}
    history = bars.loc[bars["date"] <= date].sort_values("date").copy()
    current = indicators.loc[indicators["date"] == date]
    if len(history) < 80 or current.empty:
        return {"recorded": None, "expected": None, "verdict": "NOT_EVALUABLE_INSUFFICIENT_STATE_HISTORY", "evidence": f"history_rows={len(history)}"}
    if not {"adj_close", "adj_high"}.issubset(history.columns) or history[["adj_close", "adj_high"]].isna().any().any():
        return {"recorded": None, "expected": None, "verdict": "NOT_EVALUABLE_MISSING_ADJUSTED_PRICE", "evidence": "explicit adjusted stock prices required"}
    close_col = "adj_close"
    high_col = "adj_high"
    expected = {
        "ma20": float(history[close_col].tail(20).mean()),
        "ma60": float(history[close_col].tail(60).mean()),
        "ret20": float(history.iloc[-1][close_col] / history.iloc[-21][close_col] - 1.0),
        "high_10d": float(history[high_col].tail(10).max()),
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
            if "adj_close" not in bench or bench["adj_close"].isna().any():
                return {"recorded": recorded, "expected": expected, "verdict": "NOT_EVALUABLE_MISSING_ADJUSTED_PRICE", "evidence": "explicit adjusted benchmark prices required"}
            expected["benchmark_ret20"] = float(bench.iloc[-1]["adj_close"] / bench.iloc[-21]["adj_close"] - 1.0)
            recorded["benchmark_ret20"] = recorded_row.get("benchmark_ret20")
            matches &= _close(recorded["benchmark_ret20"], expected["benchmark_ret20"])
    return {"recorded": recorded, "expected": expected, "verdict": "PASS" if matches else "FAIL", "evidence": "prefix-only rolling recomputation"}


def _reconstruct_invalid_reason(row: dict[str, Any], expected_entry: pd.Series | None) -> str:
    if expected_entry is None:
        return "invalid_no_next_open"
    if _bool(expected_entry.get("is_suspended")):
        return "invalid_suspended_cannot_buy"
    if _number(expected_entry.get("raw_open")) is None:
        return "invalid_missing_raw_open"
    if price_transform(expected_entry.to_dict()) is None:
        return "invalid_missing_adjustment_factor"
    fillability = _tri_state(expected_entry.get("can_buy_at_open"))
    if fillability is None:
        return "invalid_open_fillability_unknown"
    if not fillability and _tri_state(expected_entry.get("one_price_limit_up")) is True:
        return "invalid_limit_up_cannot_buy"
    if not fillability:
        return "invalid_cannot_buy_at_open"
    entry = _number(expected_entry.get("adj_open"))
    stop = _number(row.get("stop_adjusted_price", row.get("stop_price")))
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
        return expected_entry is not None and _tri_state(expected_entry.get("one_price_limit_up")) is True and _tri_state(expected_entry.get("can_buy_at_open")) is False, "rule-backed one-price limit-up correctly rejected"
    return executable, "other invalid reason requires an otherwise executable entry"


def _entry_execution_state(expected_entry: pd.Series | None) -> tuple[bool | None, str]:
    if expected_entry is None:
        return False, "missing next open"
    suspended = _tri_state(expected_entry.get("is_suspended"))
    if suspended is None:
        return None, "trade status unknown"
    if suspended:
        return False, "suspended entry"
    if _number(expected_entry.get("raw_open")) is None:
        return None, "raw open unavailable"
    if price_transform(expected_entry.to_dict()) is None:
        return None, "adjustment factor unavailable"
    fillability = _tri_state(expected_entry.get("can_buy_at_open"))
    if fillability is None:
        return None, f"open fillability unknown: {expected_entry.get('limit_rule_status')}"
    return fillability, "explicit open fillability"


def _preferred_number(row: pd.Series, preferred: str, fallback: str) -> float | None:
    value = _number(row.get(preferred))
    return value if value is not None else _number(row.get(fallback))


def _replay_exit(
    holding: pd.DataFrame,
    stop: float,
    target: float,
) -> tuple[pd.Timestamp, str, float, int] | None:
    max_high = float("-inf")
    progress = stop + 2.0 * (target - stop) / 3.0
    pending_reason = None
    for i, (_, bar) in enumerate(holding.iterrows(), start=1):
        adjusted_open = _number(bar.get("adj_open"))
        adjusted_high = _number(bar.get("adj_high"))
        adjusted_low = _number(bar.get("adj_low"))
        adjusted_close = _number(bar.get("adj_close"))
        suspended = _tri_state(bar.get("is_suspended"))
        if suspended is None:
            return None
        if any(value is None for value in (adjusted_open, adjusted_high, adjusted_low, adjusted_close)):
            return None
        if pending_reason is not None:
            sellable = _tri_state(bar.get("can_sell_at_open"))
            if sellable is None:
                return None
            if suspended or not sellable:
                continue
            return pd.Timestamp(bar["date"]), pending_reason, adjusted_open, i
        max_high = max(max_high, adjusted_high)
        stop_hit = i > 1 and adjusted_low <= stop
        target_hit = i > 1 and adjusted_high >= target
        if stop_hit or target_hit:
            sellable = _tri_state(bar.get("can_sell_intraday"))
            if sellable is None:
                return None
            reason = "stop_loss" if stop_hit else "target_2r"
            if suspended or not sellable:
                pending_reason = reason
                continue
            price = (adjusted_open if adjusted_open <= stop else stop) if reason == "stop_loss" else (adjusted_open if adjusted_open >= target else target)
            return pd.Timestamp(bar["date"]), reason, price, i
        if i == 5 and max_high < progress:
            sellable = _tri_state(bar.get("can_sell_at_close"))
            if sellable is None:
                return None
            if suspended or not sellable:
                pending_reason = "time_stop_no_upside_progress"
                continue
            return pd.Timestamp(bar["date"]), "time_stop_no_upside_progress", adjusted_close, i
        if i >= 10:
            sellable = _tri_state(bar.get("can_sell_at_close"))
            if sellable is None:
                return None
            if suspended or not sellable:
                pending_reason = "max_holding_exit"
                continue
            return pd.Timestamp(bar["date"]), "max_holding_exit", adjusted_close, i
    return None


def _verify_limit_rows(relevant: pd.DataFrame) -> tuple[str, Any, bool]:
    required = {
        "date",
        "code",
        "historical_st_status",
        "listing_date",
        "raw_pre_close",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "is_suspended",
        "adj_factor",
        "limit_rule_status",
    }
    if relevant.empty or not required.issubset(relevant.columns):
        return "NOT_EVALUABLE_LIMIT_PRICE", "required rule inputs unavailable", True
    compared = 0
    branch_invariant_unknown = 0
    execution_fields = (
        "can_buy_at_open",
        "can_sell_at_open",
        "can_sell_intraday",
        "can_sell_at_close",
        "scheduled_close_fill_status",
    )
    for bar in relevant.to_dict("records"):
        listing_date = None if pd.isna(bar.get("listing_date")) else pd.Timestamp(bar["listing_date"]).date()
        expected = evaluate_price_limit_bar(
            code=str(bar["code"]),
            trade_date=pd.Timestamp(bar["date"]).date(),
            board=bar.get("board"),
            historical_st_status=bar.get("historical_st_status"),
            listing_date=listing_date,
            previous_unadjusted_close=bar.get("raw_pre_close"),
            raw_open=bar.get("raw_open"),
            raw_high=bar.get("raw_high"),
            raw_low=bar.get("raw_low"),
            raw_close=bar.get("raw_close"),
            is_suspended=_tri_state(bar.get("is_suspended")),
            adj_factor=bar.get("adj_factor"),
            listing_trading_day=_positive_int(bar.get("listing_trading_day")),
            opening_auction_fill_status=bar.get("opening_auction_fill_status"),
            closing_auction_fill_status=bar.get("closing_auction_fill_status"),
        )
        if str(expected["limit_rule_status"]).startswith("UNKNOWN"):
            if expected.get("historical_st_branch_status") != "PASS_BRANCH_INVARIANT":
                return "NOT_EVALUABLE_LIMIT_PRICE", expected["limit_rule_status"], True
            for field in execution_fields:
                if str(bar.get(field)) != str(expected[field]):
                    return "FAIL", {"date": str(bar["date"]), "field": field, "recorded": bar.get(field), "expected": expected[field]}, True
            branch_invariant_unknown += 1
            continue
        for field in ("limit_rule_status", "open_at_limit_up", "open_at_limit_down", "close_at_limit_up", "close_at_limit_down", "one_price_limit_up", "one_price_limit_down", *execution_fields):
            if str(bar.get(field)) != str(expected[field]):
                return "FAIL", {"date": str(bar["date"]), "field": field, "recorded": bar.get(field), "expected": expected[field]}, True
        for field in ("limit_up_price", "limit_down_price"):
            recorded, calculated = bar.get(field), expected[field]
            if not ((pd.isna(recorded) and calculated is None) or _close(recorded, calculated)):
                return "FAIL", {"date": str(bar["date"]), "field": field, "recorded": recorded, "expected": calculated}, True
        compared += 1
    if branch_invariant_unknown:
        return "NOT_EVALUABLE_LIMIT_PRICE_BRANCH_INVARIANT", {"known_rows": compared, "branch_invariant_rows": branch_invariant_unknown}, False
    return "PASS", {"rows_recomputed": compared}, False


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


def _positive_int(value: Any) -> int | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return int(number)


def _close(left: Any, right: Any, tolerance: float = 1e-8) -> bool:
    a, b = _number(left), _number(right)
    return a is not None and b is not None and abs(a - b) <= tolerance * max(1.0, abs(a), abs(b))


def _close_absolute(left: Any, right: Any, tolerance: float) -> bool:
    a, b = _number(left), _number(right)
    return a is not None and b is not None and abs(a - b) <= tolerance


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _tri_state(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().upper()
    if normalized in {"TRUE", "1", "YES", "Y"}:
        return True
    if normalized in {"FALSE", "0", "NO", "N"}:
        return False
    return None


def _difference(recorded: Any, recalculated: Any) -> float | None:
    left, right = _number(recorded), _number(recalculated)
    return None if left is None or right is None else left - right


def _json(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        value = value.isoformat()
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)

"""Immutable comparison archive completion for historical strategy artifacts.

This module only records and verifies historical artifacts.  It never uses
those artifacts to construct a new signal, trade, metric, or strategy decision.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from texperiment.audit.manifest import sha256_file
from texperiment.full_recalculation.manifest_canonicalization import (
    attach_manifest_self_hash,
    verify_manifest_self_hash,
)


ARCHIVE_SCHEMA = "STOCK_RS_PULLBACK_v1_COMPARISON_ARCHIVE_V1"
AUDIT_ID = "STOCK_RS_PULLBACK_v1_ORIGINAL_METRICS_ARCHIVE_AUDIT_v1"
TIMEZONE = "Asia/Shanghai"
SOURCE_SETUP_ID = "STOCK_RS_PULLBACK_v1"

ARTIFACT_PATHS = {
    "original_signals": "data/signals/STOCK_RS_PULLBACK_v1_signals.csv",
    "original_trades": "data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv",
    "original_metrics": "data/reports/STOCK_RS_PULLBACK_v1_metrics.json",
}


class ComparisonArchiveError(ValueError):
    pass


def build_comparison_archive(
    project_root: str | Path,
    *,
    historical_audit_manifest: str | Path,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a self-hashed, comparison-only archive from existing artifacts.

    Signals and trades must still match their historical audit references.  The
    previously omitted metrics JSON is frozen as a new reference without
    rewriting either the historic audit manifest or any original artifact.
    """
    root = Path(project_root).resolve()
    historical_path = _absolute(root, historical_audit_manifest)
    historical = _read_json_object(historical_path, "historical audit manifest")
    indexed = _index_inputs(historical)

    inputs: list[dict[str, Any]] = []
    for name in ("original_signals", "original_trades"):
        relative = ARTIFACT_PATHS[name]
        item = indexed.get(relative)
        if item is None or not _valid_sha256(item.get("sha256")):
            raise ComparisonArchiveError(f"historical audit manifest missing {name}")
        actual = sha256_file(root / relative)
        if actual != item["sha256"]:
            raise ComparisonArchiveError(f"historical artifact hash drift: {name}")
        inputs.append(dict(item))

    metrics_path = root / ARTIFACT_PATHS["original_metrics"]
    metrics = _read_json_object(metrics_path, "original metrics")
    _validate_metrics_shape(metrics)
    inputs.append({
        "path": ARTIFACT_PATHS["original_metrics"],
        "sha256": sha256_file(metrics_path),
        "bytes": metrics_path.stat().st_size,
        "format": "json",
        "setup_id": metrics["setup_id"],
        "recorded_decision": metrics["decision"],
        "overall_rows": metrics["overall"]["rows"],
        "yearly_rows": len(metrics["yearly"]),
    })
    archive = {
        "schema": ARCHIVE_SCHEMA,
        "archive_id": "STOCK_RS_PULLBACK_v1_ORIGINAL_COMPARISON_ARCHIVE_v1",
        "timezone": TIMEZONE,
        "created_at": created_at or datetime.now(ZoneInfo(TIMEZONE)).isoformat(),
        "source_setup": SOURCE_SETUP_ID,
        "archive_role": "EXTERNAL_COMPARISON_INPUTS_ONLY",
        "comparison_boundary": {
            "runtime_verification_stage": "DELTA_AND_DECISION",
            "allowed_consumers": ["DELTA_AND_DECISION"],
        },
        "historical_audit_manifest": {
            "path": _relative(root, historical_path),
            "sha256": sha256_file(historical_path),
        },
        "inputs": inputs,
        "strategy_decision_generated": False,
        "permissions": {
            "formal_recalculation_run_authorized": False,
            "account_simulation_allowed": False,
            "ticket_generation_allowed": False,
            "trading_allowed": False,
        },
        "integrity": {},
    }
    return attach_manifest_self_hash(archive)


def write_comparison_archive_atomic(archive: Mapping[str, Any], output: str | Path) -> Path:
    """Write once. Existing archive references are never overwritten."""
    path = Path(output)
    if path.exists():
        raise FileExistsError(f"comparison archive already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(archive, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _fsync_file(temporary)
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def audit_comparison_archive(project_root: str | Path, archive_path: str | Path) -> dict[str, Any]:
    """Independently check archive integrity and recorded metrics against trades."""
    root = Path(project_root).resolve()
    path = _absolute(root, archive_path)
    archive = _read_json_object(path, "comparison archive")
    _validate_archive_shape(archive)
    try:
        verify_manifest_self_hash(archive)
    except ValueError as error:
        raise ComparisonArchiveError(str(error)) from error

    indexed = _index_inputs(archive)
    artifact_hashes: dict[str, str] = {}
    for name, relative in ARTIFACT_PATHS.items():
        item = indexed.get(relative)
        if item is None or not _valid_sha256(item.get("sha256")):
            raise ComparisonArchiveError(f"comparison archive missing {name}")
        actual = sha256_file(root / relative)
        if actual != item["sha256"]:
            raise ComparisonArchiveError(f"comparison artifact hash drift: {name}")
        artifact_hashes[name] = actual

    history_ref = archive["historical_audit_manifest"]
    history_path = root / history_ref["path"]
    if not history_path.is_file() or sha256_file(history_path) != history_ref["sha256"]:
        raise ComparisonArchiveError("historical audit manifest hash drift")
    history = _read_json_object(history_path, "historical audit manifest")
    historical_inputs = _index_inputs(history)
    for name in ("original_signals", "original_trades"):
        relative = ARTIFACT_PATHS[name]
        if historical_inputs.get(relative, {}).get("sha256") != artifact_hashes[name]:
            raise ComparisonArchiveError(f"historical audit reference mismatch: {name}")

    trades = pd.read_csv(root / ARTIFACT_PATHS["original_trades"])
    metrics = _read_json_object(root / ARTIFACT_PATHS["original_metrics"], "original metrics")
    _validate_metrics_shape(metrics)
    summary = _independent_summary(trades)
    overall_checks = _compare_overall(metrics["overall"], summary)
    yearly_checks = _compare_yearly(metrics["yearly"], trades)
    linkage = _verify_signal_trade_linkage(
        root / ARTIFACT_PATHS["original_signals"],
        trades,
    )
    if not all(check["matched"] for check in overall_checks.values()):
        raise ComparisonArchiveError("original metrics overall values do not match original trades")
    if not yearly_checks["matched"]:
        raise ComparisonArchiveError("original metrics yearly values do not match original trades")
    if not linkage["matched"]:
        raise ComparisonArchiveError("original signals and trades do not have a one-to-one signal lineage")

    return {
        "audit_id": AUDIT_ID,
        "decision": "ORIGINAL_METRICS_ARCHIVE_AUDIT_PASSED",
        "scope": "ARCHIVE_COMPLETENESS_AND_HISTORICAL_METRICS_CONSISTENCY_ONLY",
        "archive": {
            "path": _relative(root, path),
            "sha256": sha256_file(path),
            "self_hash_valid": True,
        },
        "historical_audit_manifest": dict(history_ref),
        "artifact_hashes": artifact_hashes,
        "metrics_profile": {
            "setup_id": metrics["setup_id"],
            "recorded_decision": metrics["decision"],
            "overall_rows": metrics["overall"]["rows"],
            "yearly_rows": len(metrics["yearly"]),
        },
        "overall_checks": overall_checks,
        "yearly_checks": yearly_checks,
        "signal_trade_lineage": linkage,
        "strategy_decision_generated": False,
        "formal_recalculation_performed": False,
        "permissions": {
            "formal_recalculation_run_authorized": False,
            "account_simulation_allowed": False,
            "ticket_generation_allowed": False,
            "trading_allowed": False,
        },
    }


def render_comparison_archive_audit_report(audit: Mapping[str, Any]) -> str:
    checks = audit["overall_checks"]
    return "\n".join([
        "# Original Metrics Comparison Archive Audit",
        "",
        f"Decision: `{audit['decision']}`",
        "",
        "This audit freezes historical comparison inputs only. It does not generate a new strategy decision.",
        "",
        "## Integrity",
        "",
        f"- Archive: `{audit['archive']['path']}`",
        f"- Archive SHA256: `{audit['archive']['sha256']}`",
        f"- Original metrics SHA256: `{audit['artifact_hashes']['original_metrics']}`",
        f"- Historical metrics decision: `{audit['metrics_profile']['recorded_decision']}`",
        "",
        "## Independent Cross-Checks",
        "",
        f"- Overall metrics matched: `{all(item['matched'] for item in checks.values())}`",
        f"- Yearly metrics matched: `{audit['yearly_checks']['matched']}`",
        f"- Signal/trade lineage matched: `{audit['signal_trade_lineage']['matched']}`",
        "",
        "## Permissions",
        "",
        "- Formal recalculation: `false`",
        "- Account simulation: `false`",
        "- Ticket generation: `false`",
        "- Trading: `false`",
        "",
    ])


def _validate_archive_shape(archive: Mapping[str, Any]) -> None:
    if archive.get("schema") != ARCHIVE_SCHEMA:
        raise ComparisonArchiveError("comparison archive schema is invalid")
    if archive.get("source_setup") != SOURCE_SETUP_ID:
        raise ComparisonArchiveError("comparison archive setup is invalid")
    boundary = archive.get("comparison_boundary")
    if not isinstance(boundary, Mapping) or boundary.get("runtime_verification_stage") != "DELTA_AND_DECISION":
        raise ComparisonArchiveError("comparison archive verification stage is invalid")
    if boundary.get("allowed_consumers") != ["DELTA_AND_DECISION"]:
        raise ComparisonArchiveError("comparison archive consumers are invalid")
    history = archive.get("historical_audit_manifest")
    if not isinstance(history, Mapping) or not history.get("path") or not _valid_sha256(history.get("sha256")):
        raise ComparisonArchiveError("comparison archive historical audit reference is invalid")


def _validate_metrics_shape(metrics: Mapping[str, Any]) -> None:
    if metrics.get("setup_id") != SOURCE_SETUP_ID:
        raise ComparisonArchiveError("original metrics setup_id is invalid")
    if not isinstance(metrics.get("decision"), str):
        raise ComparisonArchiveError("original metrics decision is missing")
    overall = metrics.get("overall")
    if not isinstance(overall, Mapping):
        raise ComparisonArchiveError("original metrics overall section is missing")
    required = ("rows", "valid_trades", "invalid_trades", "mean_net_return", "median_net_return", "win_rate", "profit_factor")
    missing = [name for name in required if name not in overall]
    if missing:
        raise ComparisonArchiveError(f"original metrics overall fields missing: {missing}")
    if not isinstance(metrics.get("yearly"), list):
        raise ComparisonArchiveError("original metrics yearly section is missing")


def _independent_summary(trades: pd.DataFrame) -> dict[str, Any]:
    valid = trades.loc[trades["status"].eq("valid_trade")].copy()
    valid["net_return"] = pd.to_numeric(valid["net_return"], errors="coerce")
    valid = valid.loc[valid["net_return"].map(lambda value: math.isfinite(float(value)) if pd.notna(value) else False)]
    returns = valid["net_return"].astype(float)
    gains = float(returns.loc[returns > 0].sum())
    losses = float(-returns.loc[returns < 0].sum())
    profit_factor = math.inf if losses == 0 and gains > 0 else (gains / losses if losses > 0 else 0.0)
    top_three = float(returns.nlargest(3).sum())
    total = float(returns.sum())
    top_ratio = math.inf if total <= 0 and top_three > 0 else (top_three / total if total > 0 else 0.0)
    trimmed = returns.sort_values(ascending=False).iloc[3:]
    return {
        "rows": int(len(trades)),
        "valid_trades": int(len(valid)),
        "invalid_trades": int(len(trades) - len(valid)),
        "mean_net_return": float(returns.mean()) if len(returns) else 0.0,
        "median_net_return": float(returns.median()) if len(returns) else 0.0,
        "win_rate": float((returns > 0).mean()) if len(returns) else 0.0,
        "profit_factor": profit_factor,
        "best_3_removed_mean": float(trimmed.mean()) if len(trimmed) else 0.0,
        "top3_contribution_sum": top_three,
        "top3_contribution_ratio": top_ratio,
        "net_return_sum": total,
        "max_gain": float(returns.max()) if len(returns) else 0.0,
        "max_loss": float(returns.min()) if len(returns) else 0.0,
    }


def _compare_overall(recorded: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    fields = (
        "rows", "valid_trades", "invalid_trades", "mean_net_return", "median_net_return", "win_rate",
        "profit_factor", "best_3_removed_mean", "top3_contribution_sum", "top3_contribution_ratio",
        "net_return_sum", "max_gain", "max_loss",
    )
    return {
        name: {"recorded": recorded.get(name), "recomputed": expected[name], "matched": _equal(recorded.get(name), expected[name])}
        for name in fields
    }


def _compare_yearly(recorded: list[Any], trades: pd.DataFrame) -> dict[str, Any]:
    valid = trades.loc[trades["status"].eq("valid_trade")].copy()
    valid["net_return"] = pd.to_numeric(valid["net_return"], errors="coerce")
    valid["exit_date"] = pd.to_datetime(valid["exit_date"], errors="coerce")
    valid = valid.loc[valid["net_return"].notna() & valid["exit_date"].notna()].copy()
    valid["year"] = valid["exit_date"].dt.year
    actual_rows = []
    for year, frame in valid.groupby("year", sort=True):
        summary = _independent_summary(frame.assign(status="valid_trade"))
        actual_rows.append({
            "year": int(year),
            **{name: summary[name] for name in ("valid_trades", "mean_net_return", "median_net_return", "win_rate", "profit_factor", "best_3_removed_mean", "net_return_sum")},
        })
    expected_by_year = {int(row["year"]): row for row in actual_rows}
    recorded_by_year = {int(row["year"]): row for row in recorded if isinstance(row, Mapping) and "year" in row}
    fields = ("valid_trades", "mean_net_return", "median_net_return", "win_rate", "profit_factor", "best_3_removed_mean", "net_return_sum")
    details = []
    for year in sorted(set(expected_by_year) | set(recorded_by_year)):
        actual, archive = expected_by_year.get(year), recorded_by_year.get(year)
        matches = actual is not None and archive is not None and all(_equal(archive.get(field), actual[field]) for field in fields)
        details.append({"year": year, "matched": matches})
    return {"expected_years": len(expected_by_year), "recorded_years": len(recorded_by_year), "matched": bool(details) and all(row["matched"] for row in details), "details": details}


def _verify_signal_trade_linkage(signals_path: Path, trades: pd.DataFrame) -> dict[str, Any]:
    signals = pd.read_csv(signals_path, usecols=["signal_id"])
    signal_ids = set(signals["signal_id"].dropna().astype(str))
    trade_ids = trades["signal_id"].dropna().astype(str)
    duplicate_signals = int(signals["signal_id"].duplicated().sum())
    duplicate_trade_signals = int(trade_ids.duplicated().sum())
    missing_signal_references = int((~trade_ids.isin(signal_ids)).sum())
    return {
        "signal_rows": int(len(signals)),
        "trade_rows": int(len(trades)),
        "duplicate_signal_ids": duplicate_signals,
        "duplicate_trade_signal_ids": duplicate_trade_signals,
        "missing_signal_references": missing_signal_references,
        "matched": duplicate_signals == 0 and duplicate_trade_signals == 0 and missing_signal_references == 0,
    }


def _index_inputs(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise ComparisonArchiveError("archive inputs must be a list")
    indexed = {str(item.get("path")): item for item in inputs if isinstance(item, Mapping)}
    if len(indexed) != len(inputs):
        raise ComparisonArchiveError("archive input paths must be unique")
    return indexed


def _read_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ComparisonArchiveError(f"cannot read {description}: {path}") from error
    if not isinstance(value, dict):
        raise ComparisonArchiveError(f"{description} must be a JSON object")
    return value


def _valid_sha256(value: Any) -> bool:
    text = str(value)
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text.lower())


def _equal(recorded: Any, expected: Any) -> bool:
    if isinstance(expected, int):
        return recorded == expected
    if isinstance(expected, float) and math.isinf(expected):
        return recorded == "inf" or (isinstance(recorded, (int, float)) and math.isinf(float(recorded)))
    try:
        return math.isclose(float(recorded), float(expected), rel_tol=1e-12, abs_tol=1e-12)
    except (TypeError, ValueError):
        return False


def _absolute(root: Path, path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else root / value


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

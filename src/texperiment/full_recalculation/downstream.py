from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from texperiment.audit.manifest import profile_table, sha256_file
from texperiment.backtest.engine import run_stock_rs_pullback_backtest_from_parquet, write_trades
from texperiment.backtest.trade_builder import TRADE_OUTPUT_COLUMNS
from texperiment.config.loader import load_yaml
from texperiment.full_recalculation.artifact_registry import register_artifact, require_artifact
from texperiment.full_recalculation.immutability import RecalculationAbort
from texperiment.full_recalculation.stages import StageContext, StageId, StageResult, StageStatus
from texperiment.full_recalculation.upstream import _now, _write_stage_record
from texperiment.metrics.validation import build_validation_artifacts
from texperiment.setups.stock_rs_pullback_v1.signal import (
    build_stock_rs_pullback_signals_from_parquet,
    write_signals,
)


@dataclass(frozen=True)
class RecalculatedArtifacts:
    signals: Path
    trades: Path
    metrics: Path


@dataclass(frozen=True)
class ArchivedOriginalArtifacts:
    signals: Path
    trades: Path
    metrics: Path


class SignalRebuildStage:
    stage_id = StageId.SIGNAL_REBUILD

    def __init__(self, *, batch_size: int = 250_000):
        self.batch_size = batch_size

    def run(self, context: StageContext) -> StageResult:
        started = _now()
        indicators = require_artifact(
            context.artifacts, "indicators.daily", expected_producer=StageId.INDICATOR_REBUILD
        )
        universe = require_artifact(
            context.artifacts, "universe.daily", expected_producer=StageId.UNIVERSE_REBUILD
        )
        setup = require_artifact(
            context.artifacts, "input.setup_config", expected_producer=StageId.INPUT_SNAPSHOT
        )
        stage_dir = context.work_root / self.stage_id.value
        stage_dir.mkdir(exist_ok=False)
        output = stage_dir / "signals.parquet"
        signals = build_stock_rs_pullback_signals_from_parquet(
            indicators.path,
            universe_path=universe.path,
            setup_config=load_yaml(setup.path),
            include_candidates=True,
            require_universe=True,
            batch_size=self.batch_size,
        )
        signals = _add_signal_audit_fields(signals)
        signals["setup_id"] = str(context.manifest["strategy"]["output_setup"])
        write_signals(signals, output)
        artifact = register_artifact(
            context.artifacts, name="signals.rebuilt", path=output, producer=self.stage_id
        )
        triggered = int(signals["is_final_signal"].sum()) if len(signals) else 0
        result = _result(
            self.stage_id,
            started,
            {indicators.name: indicators.sha256, universe.name: universe.sha256, setup.name: setup.sha256},
            {artifact.name: artifact.sha256},
            signals,
            warnings=(f"triggered_signals={triggered}",),
        )
        _write_stage_record(context, result)
        return result


class TradeRebuildStage:
    stage_id = StageId.TRADE_REBUILD

    def __init__(self, *, batch_size: int = 250_000):
        self.batch_size = batch_size

    def run(self, context: StageContext) -> StageResult:
        started = _now()
        signals = require_artifact(
            context.artifacts, "signals.rebuilt", expected_producer=StageId.SIGNAL_REBUILD
        )
        market = require_artifact(
            context.artifacts, "market_state.daily", expected_producer=StageId.MARKET_STATE_REBUILD
        )
        setup = require_artifact(
            context.artifacts, "input.setup_config", expected_producer=StageId.INPUT_SNAPSHOT
        )
        signal_frame = pd.read_parquet(signals.path)
        triggered = signal_frame.loc[signal_frame["is_final_signal"]].copy()
        trades = (
            pd.DataFrame(columns=TRADE_OUTPUT_COLUMNS)
            if triggered.empty
            else run_stock_rs_pullback_backtest_from_parquet(
                triggered,
                market.path,
                setup_config=load_yaml(setup.path),
                batch_size=self.batch_size,
            )
        )
        trades = _normalize_invalid_reasons(trades)
        stage_dir = context.work_root / self.stage_id.value
        stage_dir.mkdir(exist_ok=False)
        output = stage_dir / "trades.parquet"
        write_trades(trades, output)
        artifact = register_artifact(
            context.artifacts, name="trades.rebuilt", path=output, producer=self.stage_id
        )
        valid = int(trades["status"].eq("valid_trade").sum()) if len(trades) else 0
        result = _result(
            self.stage_id,
            started,
            {signals.name: signals.sha256, market.name: market.sha256, setup.name: setup.sha256},
            {artifact.name: artifact.sha256},
            trades,
            warnings=(f"valid_trades={valid}", f"invalid_trades={len(trades) - valid}"),
        )
        _write_stage_record(context, result)
        return result


class MetricsRebuildStage:
    stage_id = StageId.METRICS_REBUILD

    def run(self, context: StageContext) -> StageResult:
        started = _now()
        trades = require_artifact(
            context.artifacts, "trades.rebuilt", expected_producer=StageId.TRADE_REBUILD
        )
        signals = require_artifact(
            context.artifacts, "signals.rebuilt", expected_producer=StageId.SIGNAL_REBUILD
        )
        setup = require_artifact(
            context.artifacts, "input.setup_config", expected_producer=StageId.INPUT_SNAPSHOT
        )
        trade_frame = pd.read_parquet(trades.path)
        signal_frame = pd.read_parquet(signals.path)
        setup_config = dict(load_yaml(setup.path))
        setup_config["setup_id"] = str(context.manifest["strategy"]["output_setup"])
        artifacts = build_validation_artifacts(trade_frame, setup_config=setup_config)
        metrics = dict(artifacts["metrics"])
        metrics["overall"]["signals"] = int(signal_frame["is_final_signal"].sum())
        metrics["overall"]["holding_period_distribution"] = _counts(trade_frame, "holding_days", valid_only=True)
        stage_dir = context.work_root / self.stage_id.value
        stage_dir.mkdir(exist_ok=False)
        metrics_path = stage_dir / "metrics.json"
        yearly_path = stage_dir / "yearly.csv"
        report_path = stage_dir / "validation_report.md"
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        artifacts["yearly"].to_csv(yearly_path, index=False)
        report_path.write_text(artifacts["report_markdown"], encoding="utf-8")
        registered = {
            name: register_artifact(context.artifacts, name=name, path=path, producer=self.stage_id)
            for name, path in {
                "metrics.rebuilt": metrics_path,
                "metrics.yearly": yearly_path,
                "metrics.report": report_path,
            }.items()
        }
        result = _result(
            self.stage_id,
            started,
            {trades.name: trades.sha256, signals.name: signals.sha256, setup.name: setup.sha256},
            {name: item.sha256 for name, item in registered.items()},
            trade_frame,
        )
        _write_stage_record(context, result)
        return result


class DeltaAndDecisionStage:
    stage_id = StageId.DELTA_AND_DECISION

    def run(self, context: StageContext) -> StageResult:
        started = _now()
        signals = require_artifact(
            context.artifacts, "signals.rebuilt", expected_producer=StageId.SIGNAL_REBUILD
        )
        trades = require_artifact(
            context.artifacts, "trades.rebuilt", expected_producer=StageId.TRADE_REBUILD
        )
        metrics = require_artifact(
            context.artifacts, "metrics.rebuilt", expected_producer=StageId.METRICS_REBUILD
        )
        archived = _load_archived_inputs(context)
        delta = build_delta(
            RecalculatedArtifacts(signals.path, trades.path, metrics.path), archived
        )
        market = require_artifact(
            context.artifacts, "market_state.daily", expected_producer=StageId.MARKET_STATE_REBUILD
        )
        market_frame = pd.read_parquet(
            market.path,
            columns=["mapping_status", "st_branch_status", "is_suspended", "close_at_limit_down"],
        )
        trade_frame = pd.read_parquet(trades.path)
        delta["execution_delta"] = {
            "daily_ratio_fallback_rows": int(market_frame["mapping_status"].eq("DAILY_RATIO_FALLBACK").sum()),
            "pass_branch_invariant": int(market_frame["st_branch_status"].eq("PASS_BRANCH_INVARIANT").sum()),
            "material_st_ambiguity": int(market_frame["st_branch_status"].astype(str).str.startswith("NOT_EVALUABLE").sum()),
            "suspended_rows": _true_count(market_frame["is_suspended"]),
            "close_limit_down_rows": _true_count(market_frame["close_at_limit_down"]),
            "carried_exit_trades": int(trade_frame.get("exit_reason", pd.Series(dtype=str)).astype(str).str.contains("carry", case=False).sum()),
        }
        classification = json.loads(metrics.path.read_text(encoding="utf-8"))["decision"]
        payload = {
            "delta": delta,
            "decision_preview": {
                "classification": classification,
                "authoritative": False,
                "published": False,
            },
        }
        stage_dir = context.work_root / self.stage_id.value
        stage_dir.mkdir(exist_ok=False)
        output = stage_dir / "delta_and_decision.json"
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        artifact = register_artifact(
            context.artifacts, name="delta.preview", path=output, producer=self.stage_id
        )
        input_hashes = {
            signals.name: signals.sha256,
            trades.name: trades.sha256,
            metrics.name: metrics.sha256,
            market.name: market.sha256,
            "comparison.original_signals": sha256_file(archived.signals),
            "comparison.original_trades": sha256_file(archived.trades),
            "comparison.original_metrics": sha256_file(archived.metrics),
        }
        result = StageResult(
            stage=self.stage_id,
            status=StageStatus.PASSED,
            started_at=started,
            completed_at=_now(),
            input_hashes=input_hashes,
            output_hashes={artifact.name: artifact.sha256},
            rows=int(delta["trade_delta"]["recalculated_rows"]),
        )
        _write_stage_record(context, result)
        return result


def build_delta(new: RecalculatedArtifacts, original: ArchivedOriginalArtifacts) -> dict[str, Any]:
    new_signals = _read_table(new.signals)
    old_signals = _read_table(original.signals)
    new_trades = _read_table(new.trades)
    old_trades = _read_table(original.trades)
    new_metrics = json.loads(new.metrics.read_text(encoding="utf-8"))
    old_metrics = json.loads(original.metrics.read_text(encoding="utf-8"))
    new_triggered = new_signals.loc[new_signals["status"].eq("triggered_entry_next_open")].copy()
    old_triggered = old_signals.loc[old_signals["status"].eq("triggered_entry_next_open")].copy()
    new_keys = set(_semantic_signal_keys(new_triggered))
    old_keys = set(_semantic_signal_keys(old_triggered))
    signal_common = old_triggered.assign(_key=_semantic_signal_keys(old_triggered)).merge(
        new_triggered.assign(_key=_semantic_signal_keys(new_triggered)), on="_key", suffixes=("_original", "_recalculated")
    )
    comparable_signal_fields = ("pullback_high", "pullback_low", "stop_price", "trigger_close")
    joined = old_trades.merge(new_trades, on="signal_id", how="outer", suffixes=("_original", "_recalculated"), indicator=True)
    common = joined.loc[joined["_merge"].eq("both")].copy()
    changed_fields = ["entry_date", "entry_price", "exit_date", "exit_price", "exit_reason", "holding_days", "net_return"]
    changes = {
        field: int(_different(common.get(f"{field}_original"), common.get(f"{field}_recalculated")).sum())
        for field in changed_fields
    }
    return {
        "signal_delta": {
            "original_only": sorted(old_keys - new_keys),
            "recalculated_only": sorted(new_keys - old_keys),
            "common": len(old_keys & new_keys),
            "common_but_fields_changed": int(_any_changed(signal_common, list(comparable_signal_fields)).sum()),
        },
        "trade_delta": {
            "original_rows": len(old_trades),
            "recalculated_rows": len(new_trades),
            "original_invalid_to_recalculated_valid": _status_transition(common, False, True),
            "original_valid_to_recalculated_invalid": _status_transition(common, True, False),
            "field_changes": changes,
            "trace": common.loc[_any_changed(common, changed_fields), ["signal_id"] + [c for c in common if c.endswith("_original") or c.endswith("_recalculated")]].to_dict("records"),
        },
        "execution_delta": {},
        "metrics_delta": _metric_delta(old_metrics, new_metrics),
    }


def _load_archived_inputs(context: StageContext) -> ArchivedOriginalArtifacts:
    specs = context.manifest.get("comparison_only_inputs")
    if not isinstance(specs, Mapping):
        raise RecalculationAbort("RECALCULATION_ABORTED_PIPELINE_CONTRACT_MISMATCH", "comparison-only inputs missing")
    paths: dict[str, Path] = {}
    for name in ("original_signals", "original_trades", "original_metrics"):
        spec = specs.get(name)
        if not isinstance(spec, Mapping) or not spec.get("path") or not spec.get("sha256"):
            raise RecalculationAbort("RECALCULATION_ABORTED_PIPELINE_CONTRACT_MISMATCH", f"comparison input missing: {name}")
        path = Path(spec["path"])
        if not path.is_absolute():
            path = context.project_root / path
        if not path.is_file() or sha256_file(path) != spec["sha256"]:
            raise RecalculationAbort("RECALCULATION_ABORTED_INPUT_DRIFT", f"archived comparison changed: {name}")
        paths[name] = path
        require_artifact(
            context.artifacts,
            f"comparison.{name}",
            expected_producer=StageId.INPUT_SNAPSHOT,
        )
    return ArchivedOriginalArtifacts(paths["original_signals"], paths["original_trades"], paths["original_metrics"])


def _add_signal_audit_fields(signals: pd.DataFrame) -> pd.DataFrame:
    out = signals.copy()
    out["is_final_signal"] = out.get("status", pd.Series(dtype=str)).eq("triggered_entry_next_open")
    out["reject_reasons"] = out.get("invalid_reason")
    out["signal_key"] = out.get("signal_id")
    if len(out):
        out["signal_key"] = _semantic_signal_keys(out)
    out["planned_entry_date"] = pd.NA
    out["pass_strength_filter"] = True
    out["pass_pullback_filter"] = True
    out["pass_reclaim_trigger"] = out["is_final_signal"]
    out["pass_universe"] = out.get("is_tradable_universe_at_pullback", False).fillna(False)
    return out


def _normalize_invalid_reasons(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    mapping = {
        "invalid_suspended_cannot_buy": "INVALID_ENTRY_SUSPENDED",
        "invalid_limit_up_cannot_buy": "INVALID_ENTRY_ONE_PRICE_LIMIT_UP",
        "invalid_inconsistent_price_layers": "INVALID_ENTRY_MAPPING_UNAVAILABLE",
        "invalid_open_fillability_unknown": "NOT_EVALUABLE_EXECUTION_DATA",
        "invalid_exit_fillability_unknown": "NOT_EVALUABLE_EXECUTION_DATA",
    }
    selected = out["status"].ne("valid_trade") & out["invalid_reason"].notna()
    out.loc[selected, "invalid_reason"] = out.loc[selected, "invalid_reason"].replace(mapping).str.upper()
    return out


def _result(stage: StageId, started: str, inputs: dict[str, str], outputs: dict[str, str], frame: pd.DataFrame, *, warnings: tuple[str, ...] = ()) -> StageResult:
    dates = pd.to_datetime(frame.get("date", frame.get("signal_date", frame.get("entry_date"))), errors="coerce") if len(frame) else pd.Series(dtype="datetime64[ns]")
    return StageResult(stage, StageStatus.PASSED, started, _now(), inputs, outputs, len(frame), None if dates.dropna().empty else dates.min().date().isoformat(), None if dates.dropna().empty else dates.max().date().isoformat(), int(frame["code"].nunique()) if "code" in frame else 0, warnings)


def _read_table(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_parquet(path)


def _counts(frame: pd.DataFrame, column: str, *, valid_only: bool = False) -> dict[str, int]:
    selected = frame.loc[frame["status"].eq("valid_trade")] if valid_only and "status" in frame else frame
    return {str(k): int(v) for k, v in selected[column].value_counts(dropna=False).items()} if column in selected else {}


def _counts_value(frame: pd.DataFrame, column: str, value: str) -> int:
    return int(frame[column].eq(value).sum()) if column in frame else 0


def _true_count(values: pd.Series) -> int:
    return int(values.astype(str).str.strip().str.lower().isin({"true", "1", "1.0"}).sum())


def _different(left: pd.Series | None, right: pd.Series | None) -> pd.Series:
    if left is None or right is None:
        return pd.Series(dtype=bool)
    return ~(left.fillna("<NA>").astype(str).eq(right.fillna("<NA>").astype(str)))


def _any_changed(frame: pd.DataFrame, fields: list[str]) -> pd.Series:
    changed = pd.Series(False, index=frame.index)
    for field in fields:
        changed |= _different(frame.get(f"{field}_original"), frame.get(f"{field}_recalculated"))
    return changed


def _status_transition(frame: pd.DataFrame, old_valid: bool, new_valid: bool) -> int:
    old = frame["status_original"].eq("valid_trade")
    new = frame["status_recalculated"].eq("valid_trade")
    return int((old.eq(old_valid) & new.eq(new_valid)).sum())


def _metric_delta(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_overall = old.get("overall", {})
    new_overall = new.get("overall", {})
    fields = ("signals", "valid_trades", "invalid_trades", "mean_net_return", "median_net_return", "profit_factor", "best_3_removed_mean")
    return {field: {"original": old_overall.get(field), "recalculated": new_overall.get(field)} for field in fields}


def _semantic_signal_keys(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(index=frame.index, dtype=str)
    trigger = frame.get("trigger_date", frame.get("signal_date")).fillna("").astype(str)
    pullback = frame.get("pullback_date", frame.get("signal_date")).fillna("").astype(str)
    return frame["code"].astype(str) + ":" + pullback + ":" + trigger

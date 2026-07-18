from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from texperiment.audit.manifest import sha256_directory, sha256_file
from texperiment.audit.remediation import prepare_remediation_bars
from texperiment.backtest.cost import COST_MODEL_VERSION
from texperiment.backtest.engine import run_stock_rs_pullback_backtest
from texperiment.backtest.execution_model import EXECUTION_MODEL_VERSION
from texperiment.config.loader import load_yaml
from texperiment.market_rules.price_limit import PRICE_LIMIT_RULE_VERSION, enrich_price_limit_fields
from texperiment.metrics.validation import build_validation_artifacts, render_validation_report, write_validation_outputs

RECALCULATED_ID = "STOCK_RS_PULLBACK_v1_RECALCULATED"
RECALCULATION_MANIFEST_VERSION = "STOCK_RS_PULLBACK_v1_RECALCULATION_MANIFEST_v1"
RECALCULATION_WINDOW_ROWS = 120
DATA_LIMITATION_REASONS = {
    "invalid_entry_price",
    "invalid_exit_fillability_unknown",
    "invalid_inconsistent_price_layers",
    "invalid_missing_adjustment_factor",
    "invalid_missing_price_data",
    "invalid_missing_raw_open",
    "invalid_no_exit_data",
    "invalid_no_next_open",
    "invalid_open_fillability_unknown",
}

INPUT_PATHS = {
    "setup_config": "configs/setups/STOCK_RS_PULLBACK_v1.yaml",
    "frozen_qfq_daily": "data/processed/a_share_daily.parquet",
    "remediation_daily": "data/processed/a_share_daily_remediation.parquet",
    "indicators": "data/processed/a_share_indicators.parquet",
    "universe": "data/processed/a_share_universe_full.parquet",
    "signals": "data/signals/STOCK_RS_PULLBACK_v1_signals.csv",
    "original_trades": "data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv",
    "original_metrics": "data/reports/STOCK_RS_PULLBACK_v1_metrics.json",
    "registry": "experiment_registry.yaml",
    "remediation_manifest": "diagnostics/STOCK_RS_PULLBACK_v1/remediation_v5_passed/STOCK_RS_PULLBACK_v1_remediation_manifest.json",
    "remediation_summary": "diagnostics/STOCK_RS_PULLBACK_v1/remediation_v5_passed/STOCK_RS_PULLBACK_v1_remediation_summary.json",
}

RAW_DIRECTORIES = {
    "tdx_raw": "data/raw/tdx_text/raw",
    "tdx_qfq": "data/raw/tdx_text/qfq",
    "tdx_hfq": "data/raw/tdx_text/hfq",
}


def build_recalculation_manifest(project_root: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if _git_dirty(root):
        raise ValueError("Git worktree must be clean before freezing recalculation manifest")
    setup = load_yaml(root / INPUT_PATHS["setup_config"])
    registry = load_yaml(root / INPUT_PATHS["registry"])
    remediation = registry["engine_remediation_tasks"]["ENGINE_REMEDIATION_A_SHARE_EXECUTION_v1"]
    remediation_summary = json.loads((root / INPUT_PATHS["remediation_summary"]).read_text(encoding="utf-8"))
    if remediation.get("remediation_decision") != "REMEDIATION_AUDIT_PASSED" or remediation_summary.get("decision") != "REMEDIATION_AUDIT_PASSED":
        raise ValueError("remediation audit is not passed")
    manifest_file = Path(manifest_path)
    if not manifest_file.is_absolute():
        manifest_file = root / manifest_file
    relative_manifest = manifest_file.resolve().relative_to(root).as_posix()
    return {
        "manifest_version": RECALCULATION_MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine_git_commit": _git_commit(root),
        "git_dirty": False,
        "engine_source_sha256": sha256_directory(root / "src/texperiment"),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "uv_lock_sha256": sha256_file(root / "uv.lock"),
            "pyproject_sha256": sha256_file(root / "pyproject.toml"),
        },
        "inputs": {name: {"path": path, "sha256": sha256_file(root / path)} for name, path in INPUT_PATHS.items()},
        "raw_inputs": {name: {"path": path, "sha256": sha256_directory(root / path)} for name, path in RAW_DIRECTORIES.items()},
        "strategy_config_sha256": sha256_file(root / INPUT_PATHS["setup_config"]),
        "strategy_rules_frozen": {
            "strength_filter": setup.get("strength_filter"),
            "pullback_filter": setup.get("pullback_filter"),
            "entry": setup.get("entry"),
            "exit": setup.get("exit"),
            "cost": setup.get("cost"),
        },
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "price_limit_rule_version": PRICE_LIMIT_RULE_VERSION,
        "cost_model_version": COST_MODEL_VERSION,
        "runtime_parameters": {"window_rows": RECALCULATION_WINDOW_ROWS},
        "original_audit_commit": registry["setups"]["STOCK_RS_PULLBACK_v1"]["audit"]["locked_commit"],
        "remediation_audit_commit": remediation["remediation_audit_commit"],
        "remediation_decision": "REMEDIATION_AUDIT_PASSED",
        "allowed_post_freeze_commit_paths": [relative_manifest],
        "full_recalculation_performed": False,
        "output_id": RECALCULATED_ID,
    }


def write_recalculation_manifest(manifest: dict[str, Any], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def run_full_recalculation(
    project_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Path]:
    root = Path(project_root).resolve()
    if _git_dirty(root):
        raise ValueError("Git worktree must be clean before full recalculation")
    manifest_file = Path(manifest_path)
    if not manifest_file.is_absolute():
        manifest_file = root / manifest_file
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    verify_recalculation_manifest(root, manifest, manifest_file)
    window_rows = int(manifest["runtime_parameters"]["window_rows"])

    signals = pd.read_csv(root / INPUT_PATHS["signals"])
    signals = signals.loc[signals["status"].eq("triggered_entry_next_open")].copy()
    original_trades = pd.read_csv(root / INPUT_PATHS["original_trades"])
    _validate_signal_population(signals, original_trades)
    signals["setup_id"] = RECALCULATED_ID
    signals["trigger_date"] = pd.to_datetime(signals["trigger_date"], errors="coerce")
    setup = load_yaml(root / INPUT_PATHS["setup_config"])
    setup = dict(setup)
    setup["setup_id"] = RECALCULATED_ID

    remediation_reader = ParquetCodeReader(root / INPUT_PATHS["remediation_daily"])
    frozen_reader = ParquetCodeReader(root / INPUT_PATHS["frozen_qfq_daily"])
    outcomes = []
    remediation_columns = [
        "date", "code", "board", "listing_date", "listing_trading_day", "historical_st_status",
        "raw_pre_close", "raw_open", "raw_high", "raw_low", "raw_close",
        "adj_open", "adj_high", "adj_low", "adj_close", "hfq_open", "hfq_high", "hfq_low", "hfq_close",
        "adj_factor", "adj_offset", "adj_type", "volume", "is_suspended",
        "opening_auction_fill_status", "closing_auction_fill_status", "adjustment_status",
    ]
    frozen_columns = ["date", "code", "open", "high", "low", "close"]
    st_overrides = {
        ("300137.SZ", "2015-05-28"): "FALSE",
        ("600037.SH", "2015-05-28"): "FALSE",
    }

    for code, code_signals in signals.groupby("code", sort=True):
        bars = remediation_reader.read_code(str(code), remediation_columns)
        frozen = frozen_reader.read_code(str(code), frozen_columns)
        prepared = prepare_remediation_bars(
            bars,
            frozen,
            daily_ratio_fallback_codes={"600114.SH"},
            historical_st_overrides=st_overrides,
            enrich=False,
        )
        for signal in code_signals.to_dict("records"):
            trigger = pd.Timestamp(signal["trigger_date"])
            start = int(prepared["date"].searchsorted(trigger, side="left"))
            window = prepared.iloc[start : start + window_rows].copy()
            if window.empty:
                window = prepared.tail(1).copy()
            window = enrich_price_limit_fields(window)
            trade = run_stock_rs_pullback_backtest(pd.DataFrame([signal]), window, setup_config=setup)
            if trade.iloc[0]["invalid_reason"] == "invalid_no_exit_data" and start + window_rows < len(prepared):
                window = enrich_price_limit_fields(prepared.iloc[start:].copy())
                trade = run_stock_rs_pullback_backtest(pd.DataFrame([signal]), window, setup_config=setup)
            outcomes.append(trade.iloc[0].to_dict())

    trades = pd.DataFrame(outcomes)
    _validate_signal_population(signals, original_trades, trades)

    trade_dir = root / f"data/trades/{RECALCULATED_ID}"
    report_dir = root / f"data/reports/{RECALCULATED_ID}"
    diagnostic_dir = root / "diagnostics/STOCK_RS_PULLBACK_v1/recalculation_v1"
    final_dirs = (trade_dir, report_dir, diagnostic_dir)
    temp_dirs = _prepare_output_directories(final_dirs)
    temp_trade_dir, temp_report_dir, temp_diagnostic_dir = temp_dirs
    trade_path = temp_trade_dir / f"{RECALCULATED_ID}_trades.csv"
    trades.to_csv(trade_path, index=False, encoding="utf-8-sig")

    artifacts = build_validation_artifacts(trades, setup_config=setup)
    original_metrics = json.loads((root / INPUT_PATHS["original_metrics"]).read_text(encoding="utf-8"))
    material = trades.loc[trades["invalid_reason"].isin(DATA_LIMITATION_REASONS)]
    unexpected = trades.loc[trades["status"].ne("valid_trade") & ~trades["invalid_reason"].isin(_allowed_invalid_reasons())]
    if not material.empty or not unexpected.empty:
        decision = "RECALCULATION_INCONCLUSIVE_DATA_LIMITATION"
    else:
        decision = _map_validation_decision(str(artifacts["metrics"]["decision"]))
    artifacts["metrics"]["decision"] = decision
    artifacts["report_markdown"] = render_validation_report(artifacts["metrics"])
    metric_paths = write_validation_outputs(
        artifacts,
        metrics_path=temp_report_dir / f"{RECALCULATED_ID}_metrics.json",
        report_path=temp_report_dir / f"{RECALCULATED_ID}_validation_report.md",
        yearly_path=temp_report_dir / f"{RECALCULATED_ID}_yearly.csv",
        industry_path=temp_report_dir / f"{RECALCULATED_ID}_industry.csv",
    )

    delta = build_delta_summary(original_trades, trades, original_metrics, artifacts["metrics"])
    delta["decision"] = decision
    delta["material_blocking_trade_count"] = int(len(material))
    delta["unexpected_invalid_outcomes"] = int(len(unexpected))
    delta_path = temp_diagnostic_dir / f"{RECALCULATED_ID}_delta_report.md"
    delta_path.write_text(render_delta_report(delta), encoding="utf-8")
    summary_path = temp_diagnostic_dir / f"{RECALCULATED_ID}_summary.json"
    summary_path.write_text(json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime_path = temp_diagnostic_dir / f"{RECALCULATED_ID}_runtime.json"
    runtime_path.write_text(json.dumps({
        "runtime_git_commit": _git_commit(root),
        "engine_source_sha256": sha256_directory(root / "src/texperiment"),
        "signals_processed": len(signals),
        "outcomes_written": len(trades),
        "full_recalculation_performed": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    for temp, final in zip(temp_dirs, final_dirs):
        os.replace(temp, final)
    return {
        "trades": trade_dir / trade_path.name,
        "metrics": report_dir / Path(metric_paths["metrics"]).name,
        "report": report_dir / Path(metric_paths["report"]).name,
        "yearly": report_dir / Path(metric_paths["yearly"]).name,
        "industry": report_dir / Path(metric_paths["industry"]).name,
        "delta_report": diagnostic_dir / delta_path.name,
        "summary": diagnostic_dir / summary_path.name,
        "runtime": diagnostic_dir / runtime_path.name,
    }


def verify_recalculation_manifest(root: Path, manifest: dict[str, Any], manifest_path: str | Path) -> None:
    _validate_manifest_schema(manifest)
    manifest_file = Path(manifest_path).resolve()
    relative_manifest = manifest_file.relative_to(root.resolve()).as_posix()
    if manifest["allowed_post_freeze_commit_paths"] != [relative_manifest]:
        raise ValueError("runtime manifest path does not match frozen post-freeze path")
    if manifest.get("git_dirty") is not False:
        raise ValueError("recalculation manifest was not frozen from a clean worktree")
    engine_commit = str(manifest["engine_git_commit"])
    if subprocess.run(["git", "merge-base", "--is-ancestor", engine_commit, "HEAD"], cwd=root).returncode != 0:
        raise ValueError("frozen engine commit is not an ancestor of runtime HEAD")
    changed_paths = set(subprocess.check_output(["git", "diff", "--name-only", f"{engine_commit}..HEAD"], cwd=root, text=True).splitlines())
    allowed_paths = set(manifest["allowed_post_freeze_commit_paths"])
    post_freeze_commits = int(subprocess.check_output(["git", "rev-list", "--count", f"{engine_commit}..HEAD"], cwd=root, text=True))
    if changed_paths != allowed_paths or post_freeze_commits != 1:
        raise ValueError("runtime HEAD must be the single manifest-only commit after frozen engine commit")
    for commit in (manifest["original_audit_commit"], manifest["remediation_audit_commit"]):
        if subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=root).returncode != 0:
            raise ValueError(f"audit provenance commit does not exist: {commit}")
    if sha256_directory(root / "src/texperiment") != manifest.get("engine_source_sha256"):
        raise ValueError("recalculation engine source changed after manifest freeze")
    changed = []
    for item in [*manifest.get("inputs", {}).values()]:
        if sha256_file(root / item["path"]) != item["sha256"]:
            changed.append(item["path"])
    for item in [*manifest.get("raw_inputs", {}).values()]:
        if sha256_directory(root / item["path"]) != item["sha256"]:
            changed.append(item["path"])
    if changed:
        raise ValueError(f"recalculation inputs changed: {changed}")
    registry = load_yaml(root / INPUT_PATHS["registry"])
    remediation = registry["engine_remediation_tasks"]["ENGINE_REMEDIATION_A_SHARE_EXECUTION_v1"]
    experiment = registry["Trading_Experiment"]
    original_audit = registry["setups"]["STOCK_RS_PULLBACK_v1"]["audit"]
    remediation_summary = json.loads((root / INPUT_PATHS["remediation_summary"]).read_text(encoding="utf-8"))
    remediation_manifest = json.loads((root / INPUT_PATHS["remediation_manifest"]).read_text(encoding="utf-8"))
    if experiment.get("status") != "recalculation_authorized" or experiment.get("trading_allowed") is not False or remediation.get("full_recalculation_allowed") is not True:
        raise ValueError("registry does not authorize research-only full recalculation")
    if original_audit.get("locked_commit") != manifest["original_audit_commit"] or remediation_manifest.get("baseline_commit") != manifest["original_audit_commit"]:
        raise ValueError("original audit provenance mismatch")
    if remediation.get("remediation_audit_commit") != manifest["remediation_audit_commit"] or remediation.get("remediation_decision") != manifest["remediation_decision"] or remediation_summary.get("decision") != "REMEDIATION_AUDIT_PASSED":
        raise ValueError("remediation approval provenance mismatch")
    if remediation.get("data_layer", {}).get("sha256") != manifest["inputs"]["remediation_daily"]["sha256"]:
        raise ValueError("registry remediation data hash mismatch")
    environment = manifest["environment"]
    if sha256_file(root / "uv.lock") != environment["uv_lock_sha256"] or sha256_file(root / "pyproject.toml") != environment["pyproject_sha256"]:
        raise ValueError("runtime dependency declarations changed after manifest freeze")


def _validate_manifest_schema(manifest: dict[str, Any]) -> None:
    required = {
        "manifest_version", "created_at", "engine_git_commit", "git_dirty", "engine_source_sha256",
        "environment", "inputs", "raw_inputs", "strategy_config_sha256", "strategy_rules_frozen",
        "execution_model_version", "price_limit_rule_version", "cost_model_version",
        "runtime_parameters",
        "original_audit_commit", "remediation_audit_commit", "remediation_decision",
        "allowed_post_freeze_commit_paths", "full_recalculation_performed", "output_id",
    }
    if set(manifest) != required:
        raise ValueError(f"recalculation manifest fields mismatch: {sorted(set(manifest) ^ required)}")
    if manifest["manifest_version"] != RECALCULATION_MANIFEST_VERSION:
        raise ValueError("unsupported recalculation manifest version")
    if set(manifest["inputs"]) != set(INPUT_PATHS) or set(manifest["raw_inputs"]) != set(RAW_DIRECTORIES):
        raise ValueError("recalculation manifest input set is incomplete")
    for name, path in INPUT_PATHS.items():
        if set(manifest["inputs"][name]) != {"path", "sha256"} or manifest["inputs"][name]["path"] != path:
            raise ValueError(f"recalculation manifest input contract invalid: {name}")
    for name, path in RAW_DIRECTORIES.items():
        if set(manifest["raw_inputs"][name]) != {"path", "sha256"} or manifest["raw_inputs"][name]["path"] != path:
            raise ValueError(f"recalculation manifest raw input contract invalid: {name}")
    if set(manifest["environment"]) != {"python", "implementation", "platform", "uv_lock_sha256", "pyproject_sha256"}:
        raise ValueError("recalculation manifest environment contract invalid")
    if set(manifest["strategy_rules_frozen"]) != {"strength_filter", "pullback_filter", "entry", "exit", "cost"}:
        raise ValueError("recalculation manifest strategy rule contract invalid")
    if manifest["strategy_config_sha256"] != manifest["inputs"]["setup_config"]["sha256"]:
        raise ValueError("recalculation strategy config hash mismatch")
    if manifest["execution_model_version"] != EXECUTION_MODEL_VERSION or manifest["price_limit_rule_version"] != PRICE_LIMIT_RULE_VERSION or manifest["cost_model_version"] != COST_MODEL_VERSION:
        raise ValueError("recalculation model version mismatch")
    if manifest["runtime_parameters"] != {"window_rows": RECALCULATION_WINDOW_ROWS}:
        raise ValueError("recalculation runtime parameters mismatch")
    if manifest["remediation_decision"] != "REMEDIATION_AUDIT_PASSED":
        raise ValueError("recalculation remediation decision is not passed")
    if manifest["output_id"] != RECALCULATED_ID or manifest["full_recalculation_performed"] is not False:
        raise ValueError("recalculation manifest output contract invalid")
    if not manifest["original_audit_commit"] or not manifest["remediation_audit_commit"]:
        raise ValueError("recalculation audit commit provenance missing")
    allowed = manifest["allowed_post_freeze_commit_paths"]
    if not isinstance(allowed, list) or len(allowed) != 1 or Path(allowed[0]).is_absolute() or ".." in Path(allowed[0]).parts:
        raise ValueError("recalculation post-freeze path contract invalid")


class ParquetCodeReader:
    def __init__(self, path: str | Path):
        self.parquet = pq.ParquetFile(path)
        code_index = self.parquet.schema.names.index("code")
        self.row_groups: dict[str, list[int]] = {}
        for index in range(self.parquet.num_row_groups):
            column = self.parquet.metadata.row_group(index).column(code_index)
            stats = column.statistics
            code = None if stats is None or stats.min != stats.max else str(stats.min)
            if code is None:
                values = self.parquet.read_row_group(index, columns=["code"]).column("code").to_pylist()
                unique = {str(value) for value in values}
                for value in unique:
                    self.row_groups.setdefault(value, []).append(index)
                continue
            self.row_groups.setdefault(code, []).append(index)

    def read_code(self, code: str, columns: list[str]) -> pd.DataFrame:
        groups = self.row_groups.get(code)
        if not groups:
            raise ValueError(f"code missing from Parquet: {code}")
        frame = self.parquet.read_row_groups(groups, columns=columns).to_pandas()
        return frame.loc[frame["code"].astype(str).eq(code)].reset_index(drop=True)


def build_delta_summary(
    original_trades: pd.DataFrame,
    new_trades: pd.DataFrame,
    original_metrics: dict[str, Any],
    new_metrics: dict[str, Any],
) -> dict[str, Any]:
    joined = original_trades[["signal_id", "status", "invalid_reason", "exit_reason", "net_return"]].merge(
        new_trades[["signal_id", "status", "invalid_reason", "exit_reason", "net_return", "holding_days"]],
        on="signal_id",
        how="outer",
        suffixes=("_original", "_recalculated"),
        indicator=True,
    )
    delayed = new_trades.loc[
        (new_trades["exit_reason"].eq("time_stop_no_upside_progress") & new_trades["holding_days"].gt(5))
        | (new_trades["exit_reason"].eq("max_holding_exit") & new_trades["holding_days"].gt(10))
    ].copy()
    delayed_days = delayed.apply(
        lambda row: row["holding_days"] - (5 if row["exit_reason"] == "time_stop_no_upside_progress" else 10),
        axis=1,
    ) if not delayed.empty else pd.Series(dtype=float)
    original_overall = original_metrics["overall"]
    new_overall = new_metrics["overall"]
    metric_keys = [
        "valid_trades", "invalid_trades", "mean_net_return", "median_net_return", "profit_factor", "win_rate",
        "best_3_removed_mean", "top3_contribution_ratio", "max_gain", "max_loss",
    ]
    metric_delta = {
        key: {"original": original_overall.get(key), "recalculated": new_overall.get(key)} for key in metric_keys
    }
    original_yearly = pd.DataFrame(original_metrics.get("yearly", []))
    new_yearly = pd.DataFrame(new_metrics.get("yearly", []))
    if "year" not in original_yearly:
        original_yearly["year"] = pd.Series(dtype="Int64")
    if "year" not in new_yearly:
        new_yearly["year"] = pd.Series(dtype="Int64")
    original_yearly = original_yearly.add_suffix("_original")
    new_yearly = new_yearly.add_suffix("_recalculated")
    yearly = original_yearly.merge(
        new_yearly,
        left_on="year_original",
        right_on="year_recalculated",
        how="outer",
    ).to_dict("records")
    return {
        "rows": {"original": len(original_trades), "recalculated": len(new_trades)},
        "valid_trades": metric_delta["valid_trades"],
        "invalid_trades": metric_delta["invalid_trades"],
        "status_transitions": joined.groupby(["status_original", "status_recalculated"], dropna=False).size().rename("count").reset_index().to_dict("records"),
        "fixed_limit_up_exclusions": int((joined["invalid_reason_original"].eq("invalid_limit_up_cannot_buy") & joined["status_recalculated"].eq("valid_trade")).sum()),
        "new_valid_entries": int((joined["status_original"].ne("valid_trade") & joined["status_recalculated"].eq("valid_trade")).sum()),
        "lost_valid_entries": int((joined["status_original"].eq("valid_trade") & joined["status_recalculated"].ne("valid_trade")).sum()),
        "invalid_reason_counts": {
            "original": original_trades.loc[original_trades["status"].ne("valid_trade"), "invalid_reason"].value_counts().to_dict(),
            "recalculated": new_trades.loc[new_trades["status"].ne("valid_trade"), "invalid_reason"].value_counts().to_dict(),
        },
        "exit_reason_counts": {
            "original": original_trades.loc[original_trades["status"].eq("valid_trade"), "exit_reason"].value_counts().to_dict(),
            "recalculated": new_trades.loc[new_trades["status"].eq("valid_trade"), "exit_reason"].value_counts().to_dict(),
        },
        "scheduled_close_delays": {
            "count": int(len(delayed)),
            "average_delay_days": 0.0 if delayed_days.empty else float(delayed_days.mean()),
            "max_delay_days": 0 if delayed_days.empty else int(delayed_days.max()),
        },
        "metrics": metric_delta,
        "yearly": yearly,
    }


def render_delta_report(delta: dict[str, Any]) -> str:
    lines = [
        f"# {RECALCULATED_ID} Delta Report",
        "",
        f"Decision: **{delta.get('decision')}**",
        "",
        "## Execution impact",
        "",
        f"- Original rows: `{delta['rows']['original']}`",
        f"- Recalculated rows: `{delta['rows']['recalculated']}`",
        f"- Fixed limit-up exclusions: `{delta['fixed_limit_up_exclusions']}`",
        f"- New valid entries: `{delta['new_valid_entries']}`",
        f"- Lost valid entries: `{delta['lost_valid_entries']}`",
        f"- Deferred scheduled-close exits: `{delta['scheduled_close_delays']['count']}`",
        f"- Average delay: `{delta['scheduled_close_delays']['average_delay_days']}` trading days",
        f"- Material blocking trades: `{delta.get('material_blocking_trade_count', 0)}`",
        f"- Unexpected invalid outcomes: `{delta.get('unexpected_invalid_outcomes', 0)}`",
        "",
        "## Metrics",
        "",
        "| Metric | Original | Recalculated |",
        "|---|---:|---:|",
    ]
    for key, values in delta["metrics"].items():
        lines.append(f"| {key} | {values.get('original')} | {values.get('recalculated')} |")
    lines += ["", "## Invalid reasons", "", f"- Original: `{delta['invalid_reason_counts']['original']}`", f"- Recalculated: `{delta['invalid_reason_counts']['recalculated']}`", "", "## Exit reasons", "", f"- Original: `{delta['exit_reason_counts']['original']}`", f"- Recalculated: `{delta['exit_reason_counts']['recalculated']}`", "", "## Yearly delta", "", "```json", json.dumps(delta["yearly"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def _map_validation_decision(decision: str) -> str:
    return "CONFIRMED_FAILED_ARCHIVED" if decision == "FAILED_ARCHIVED" else decision


def _allowed_invalid_reasons() -> set[str]:
    return {
        "invalid_cannot_buy_at_open", "invalid_entry_price", "invalid_limit_up_cannot_buy",
        "invalid_no_exit_data", "invalid_no_next_open", "invalid_signal_status",
        "invalid_stop_not_below_entry", "invalid_suspended_cannot_buy",
        *DATA_LIMITATION_REASONS,
    }


def _prepare_output_directories(final_dirs: tuple[Path, ...]) -> tuple[Path, ...]:
    existing = [str(path) for path in final_dirs if path.exists()]
    if existing:
        raise FileExistsError(f"recalculation outputs already exist; refusing overwrite: {existing}")
    temp_dirs = tuple(path.with_name(f".{path.name}.{os.getpid()}.tmp") for path in final_dirs)
    for path in temp_dirs:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=False)
    return temp_dirs


def _validate_signal_population(
    signals: pd.DataFrame,
    original_trades: pd.DataFrame,
    recalculated_trades: pd.DataFrame | None = None,
) -> None:
    populations = {"frozen signals": signals, "original trades": original_trades}
    if recalculated_trades is not None:
        populations["recalculated trades"] = recalculated_trades
    for name, frame in populations.items():
        if "signal_id" not in frame or frame["signal_id"].isna().any() or not frame["signal_id"].is_unique:
            raise ValueError(f"{name} require non-null unique signal_id")
    expected = set(signals["signal_id"].astype(str))
    if set(original_trades["signal_id"].astype(str)) != expected:
        raise ValueError("frozen signal IDs do not match original trade population")
    if recalculated_trades is not None and (
        len(recalculated_trades) != len(signals)
        or set(recalculated_trades["signal_id"].astype(str)) != expected
    ):
        raise ValueError("full recalculation did not produce one outcome per signal")


def _git_commit(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _git_dirty(root: Path) -> bool:
    return bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from texperiment.audit.manifest import sha256_file
from texperiment.config.loader import load_yaml
from texperiment.full_recalculation.artifact_registry import build_artifact_registry, register_artifact
from texperiment.full_recalculation.downstream import (
    DeltaAndDecisionStage,
    MetricsRebuildStage,
    SignalRebuildStage,
    TradeRebuildStage,
)
from texperiment.full_recalculation.formal_manifest import FormalManifestSpec, build_formal_manifest_v2, write_formal_manifest_atomic
from texperiment.full_recalculation.manifest_validation import read_and_validate_formal_manifest_v2
from texperiment.full_recalculation.runner import FullPipelineRunner
from texperiment.full_recalculation.stages import StageContext, StageId
from texperiment.full_recalculation.upstream import IndicatorRebuildStage, InputSnapshotStage, MarketStateRebuildStage, UniverseRebuildStage, _write_stage_record


def freeze_v2_from_args(args) -> int:
    root = Path(args.root).resolve()
    task = _manifest_task(root)
    _require_freeze_authorized(task)
    output = _resolve(root, args.output)
    run_id = args.run_id
    data_root = root / "data"
    registry = build_artifact_registry(data_root, root / "diagnostics", run_id)
    spec = FormalManifestSpec(
        run_id=run_id,
        raw_daily=Path(args.raw_daily),
        qfq_daily=Path(args.qfq_daily),
        benchmark=Path(args.benchmark),
        setup_config=Path(args.setup_config),
        cost_config=Path(args.cost_config),
        st_overrides=Path(args.st_overrides),
        archive_manifest=Path(args.archive_manifest),
        temporary_root=registry.temporary_root,
        final_root=registry.final_root,
        manifest_tool_commit=str(task["manifest_tool_commit"]),
        manifest_tool_audit_record_commit=str(task["manifest_tool_audit_record_commit"]),
    )
    manifest = build_formal_manifest_v2(root, spec)
    readback = write_formal_manifest_atomic(manifest, output)
    read_and_validate_formal_manifest_v2(root, readback, verify_core_files=True)
    print(f"freeze-stock-rs-pullback-recalculation-v2: OK -> {readback}")
    print(json.dumps({"run_id": run_id, "manifest_self_sha256": manifest["integrity"]["manifest_self_sha256"]}, indent=2))
    return 0


def validate_v2_from_args(args) -> int:
    root = Path(args.root).resolve()
    manifest = read_and_validate_formal_manifest_v2(root, args.manifest, verify_core_files=True)
    print("validate-stock-rs-pullback-recalculation-manifest-v2: OK")
    print(json.dumps({"run_id": manifest["manifest"]["run_id"]}, indent=2))
    return 0


def run_v2_from_args(args) -> int:
    root = Path(args.root).resolve()
    task = _manifest_task(root)
    if task.get("formal_recalculation_run_authorized") is not True:
        raise PermissionError("formal V2 recalculation run is not authorized")
    manifest = read_and_validate_formal_manifest_v2(root, args.manifest, verify_core_files=True)
    runtime_manifest = _authorized_runtime_view(manifest)
    run_id = str(manifest["manifest"]["run_id"])
    temporary = root / manifest["outputs"]["temporary_root"]
    final = root / manifest["outputs"]["final_root"]
    context = StageContext(
        run_id=run_id,
        project_root=root,
        work_root=temporary,
        final_root=final,
        failure_root=root / "diagnostics" / "recalculation_attempts" / run_id,
        manifest=runtime_manifest,
    )
    stages = _formal_stages(root, manifest)
    FullPipelineRunner(stages).run(context)
    print(f"run-stock-rs-pullback-recalculation-v2: OK -> {final}")
    return 0


class ArchiveVerifiedDeltaStage:
    stage_id = StageId.DELTA_AND_DECISION

    def __init__(self, root: Path, manifest: dict[str, Any]):
        self.root = root
        self.manifest = manifest
        self.delegate = DeltaAndDecisionStage()

    def run(self, context: StageContext):
        reference = self.manifest["comparison_inputs"]["archive_manifest"]
        path = self.root / reference["path"]
        if not path.is_file():
            raise FileNotFoundError("frozen comparison archive manifest is missing")
        if sha256_file(path) != reference["expected_sha256"]:
            raise ValueError("RECALCULATION_ABORTED_ARCHIVE_MANIFEST_MISMATCH")
        archive = register_artifact(
            context.artifacts,
            name="comparison.archive_manifest",
            path=path,
            producer=None,
            source_class="EXTERNAL_COMPARISON_INPUT",
            registered_by_stage=StageId.DELTA_AND_DECISION,
            allowed_consumers=(StageId.DELTA_AND_DECISION,),
        )
        result = self.delegate.run(context)
        result = replace(
            result,
            input_hashes={**result.input_hashes, archive.name: archive.sha256},
        )
        _write_stage_record(context, result)
        return result


def _formal_stages(root: Path, manifest: dict[str, Any]):
    return {
        StageId.INPUT_SNAPSHOT: InputSnapshotStage(),
        StageId.MARKET_STATE_REBUILD: MarketStateRebuildStage(),
        StageId.UNIVERSE_REBUILD: UniverseRebuildStage(),
        StageId.INDICATOR_REBUILD: IndicatorRebuildStage(),
        StageId.SIGNAL_REBUILD: SignalRebuildStage(),
        StageId.TRADE_REBUILD: TradeRebuildStage(),
        StageId.METRICS_REBUILD: MetricsRebuildStage(),
        StageId.DELTA_AND_DECISION: ArchiveVerifiedDeltaStage(root, manifest),
    }


def _manifest_task(root: Path) -> dict[str, Any]:
    registry = load_yaml(root / "experiment_registry.yaml")
    return registry.get("full_pipeline_recalculation_tasks", {}).get(
        "FULL_PIPELINE_RECALCULATION_MANIFEST_V2_IMPLEMENTATION", {}
    )


def _require_freeze_authorized(task: dict[str, Any]) -> None:
    if not (
        task.get("status") == "manifest_freeze_authorized"
        and task.get("manifest_v2_audited") is True
        and task.get("manifest_v2_audit_decision") == "MANIFEST_V2_AUDIT_PASSED"
        and task.get("formal_recalculation_run_authorized") is False
        and len(str(task.get("manifest_tool_commit", ""))) == 40
        and len(str(task.get("manifest_tool_audit_record_commit", ""))) == 40
    ):
        raise PermissionError("Manifest V2 freeze is not audited and authorized")


def _authorized_runtime_view(manifest: dict[str, Any]) -> dict[str, Any]:
    """Adapt a validated frozen snapshot to the audited runner's legacy gate."""
    if manifest["authorization_snapshot"]["formal_recalculation_run_authorized"] is not False:
        raise PermissionError("frozen Manifest must not contain mutable run authorization")
    capabilities = manifest["run_capabilities"]
    if capabilities.get("strategy_validation_classification_output") is not True:
        raise PermissionError("formal run lacks validation classification capability")
    for name in ("account_simulation_output", "ticket_generation_output", "trading_output"):
        if capabilities.get(name) is not False:
            raise PermissionError(f"formal run capability is unsafe: {name}")
    runtime = deepcopy(manifest)
    runtime["permissions"]["full_recalculation_allowed"] = True
    return runtime


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path

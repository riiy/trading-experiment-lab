from __future__ import annotations

import json
import os
import hashlib
import stat
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from texperiment.audit.manifest import sha256_file
from collections.abc import Mapping

from texperiment.full_recalculation.contract import EXPECTED_STAGES
from texperiment.full_recalculation.schema import validate_manifest_v2
from texperiment.full_recalculation.stages import (
    RecalculationStage,
    StageContext,
    StageId,
    StageResult,
    StageStatus,
)


class StageExecutionError(RuntimeError):
    def __init__(self, stage: StageId, results: tuple[StageResult, ...], message: str):
        super().__init__(message)
        self.stage = stage
        self.results = results


class FullPipelineRunner:
    """Order-only V2 orchestrator. Domain work remains in injected stage implementations."""

    def __init__(self, stages: Mapping[StageId, RecalculationStage]):
        expected = tuple(StageId(name) for name in EXPECTED_STAGES)
        if tuple(stages) != expected:
            raise ValueError("stage implementations must match the exact V2 order")
        for stage_id, stage in stages.items():
            if stage.stage_id != stage_id:
                raise ValueError(f"stage implementation ID mismatch: {stage_id.value}")
        self._stages = dict(stages)

    def run(self, context: StageContext) -> tuple[StageResult, ...]:
        if context.manifest.get("permissions", {}).get("full_recalculation_allowed") is not True:
            raise PermissionError("full pipeline execution is not authorized")
        results = self.run_until(context, StageId.DELTA_AND_DECISION)
        try:
            _publish_completed_run(context, results)
        except Exception as exc:
            error = StageExecutionError(
                StageId.DELTA_AND_DECISION,
                results,
                "publication failed after all stages completed",
            )
            _quarantine_failure(context, error)
            raise error from exc
        return results

    def run_until(self, context: StageContext, final_stage: StageId) -> tuple[StageResult, ...]:
        validate_manifest_v2(context.manifest)
        results: list[StageResult] = []
        for stage_id, stage in self._stages.items():
            try:
                result = stage.run(context)
            except Exception as exc:
                error = StageExecutionError(
                    stage_id,
                    tuple(results),
                    f"stage failed before completion: {stage_id.value}",
                )
                _quarantine_failure(context, error)
                raise error from exc
            if result.stage != stage_id:
                error = StageExecutionError(stage_id, tuple(results), "stage returned the wrong ID")
                _quarantine_failure(context, error)
                raise error
            results.append(result)
            if result.status != StageStatus.PASSED or result.blocking_errors:
                error = StageExecutionError(stage_id, tuple(results), f"stage failed: {stage_id.value}")
                _quarantine_failure(context, error)
                raise error
            if stage_id == final_stage:
                return tuple(results)
        raise ValueError(f"final stage is not registered: {final_stage.value}")


def _quarantine_failure(context: StageContext, error: StageExecutionError) -> None:
    if context.failure_root is None:
        return
    if context.failure_root.exists():
        raise FileExistsError(f"failure diagnostics already exist: {context.failure_root}")
    context.failure_root.parent.mkdir(parents=True, exist_ok=True)
    if context.work_root.exists():
        os.replace(context.work_root, context.failure_root)
    else:
        context.failure_root.mkdir()
    context.failure_root.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    (context.failure_root / "failure.json").write_text(
        json.dumps(
            {
                "decision": "RECALCULATION_ABORTED_STAGE_FAILURE",
                "failed_stage": error.stage.value,
                "completed_stages": [result.stage.value for result in error.results],
                "strategy_decision_generated": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _publish_completed_run(context: StageContext, results: tuple[StageResult, ...]) -> None:
    if context.final_root is None:
        raise ValueError("formal run requires final_root")
    if context.final_root.exists():
        raise FileExistsError(f"formal output already exists: {context.final_root}")
    if tuple(result.stage for result in results) != tuple(StageId(name) for name in EXPECTED_STAGES):
        raise ValueError("cannot publish an incomplete or out-of-order run")
    _assert_closed_permissions(context)
    chain = _build_and_verify_persisted_chain(context)
    chain_path = context.work_root / "artifact_hash_chain.json"
    chain_path.write_text(json.dumps(chain, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tree_hash = _tree_sha256(context.work_root, excluded={"publication.json"})
    publication = {
        "publication": {
            "status": "PUBLISHED",
            "source_tmp_root": str(context.work_root),
            "final_root": str(context.final_root),
            "published_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "atomic_move_verified": True,
            "final_tree_sha256": tree_hash,
            "tree_hash_excludes": ["publication.json"],
        },
        "final_artifacts": {
            name: artifact.sha256 for name, artifact in sorted(context.artifacts.items())
        },
    }
    (context.work_root / "publication.json").write_text(
        json.dumps(publication, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    _fsync_tree(context.work_root)
    _make_tree_read_only(context.work_root)
    context.final_root.parent.mkdir(parents=True, exist_ok=True)
    if os.stat(context.work_root).st_dev != os.stat(context.final_root.parent).st_dev:
        raise OSError("temporary and final roots are on different filesystems")
    os.replace(context.work_root, context.final_root)
    if _tree_sha256(context.final_root, excluded={"publication.json"}) != tree_hash:
        raise OSError("published final tree hash mismatch")


def _build_and_verify_persisted_chain(context: StageContext) -> dict[str, object]:
    records: list[dict[str, object]] = []
    produced: dict[str, dict[str, object]] = {}
    for sequence, stage_name in enumerate(EXPECTED_STAGES, start=1):
        path = context.work_root / stage_name / "stage.json"
        if not path.is_file():
            raise ValueError(f"missing stage record: {stage_name}")
        record = json.loads(path.read_text(encoding="utf-8"))
        stage = record.get("stage", {})
        if stage != {"id": stage_name, "sequence": sequence, "status": "PASSED"}:
            raise ValueError(f"invalid stage identity or order: {stage_name}")
        for item in record.get("inputs", []):
            if item["expected_sha256"] != item["verified_sha256"]:
                raise ValueError(f"unverified input hash: {item['artifact_id']}")
            producer = produced.get(item["artifact_id"])
            if producer is None and stage_name != StageId.INPUT_SNAPSHOT.value:
                raise ValueError(f"input has no prior producer: {item['artifact_id']}")
            if producer is not None and (
                producer["producer_stage"] != item["producer_stage"]
                or producer["sha256"] != item["verified_sha256"]
            ):
                raise ValueError(f"input chain mismatch: {item['artifact_id']}")
        for output in record.get("outputs", []):
            artifact_id = output["artifact_id"]
            if artifact_id in produced:
                raise ValueError(f"duplicate artifact producer: {artifact_id}")
            if output["producer_stage"] != stage_name:
                raise ValueError(f"output producer mismatch: {artifact_id}")
            artifact_path = _resolve_recorded_artifact_path(context, str(output["path"]))
            if not artifact_path.is_file() or sha256_file(artifact_path) != output["sha256"]:
                raise ValueError(f"output artifact bytes changed: {artifact_id}")
            produced[artifact_id] = output
        records.append(record)
    return {"contract": "FULL_PIPELINE_RECALCULATION_V2", "stages": records}


def _resolve_recorded_artifact_path(context: StageContext, recorded_path: str) -> Path:
    path = Path(recorded_path)
    if path.is_absolute():
        return path
    work_path = context.work_root / path
    return work_path if work_path.exists() else context.project_root / path


def _assert_closed_permissions(context: StageContext) -> None:
    permissions = context.manifest.get("permissions", {})
    for name in ("trading_allowed", "account_simulation_allowed", "ticket_generation_allowed"):
        if permissions.get(name) is not False:
            raise PermissionError(f"publication requires closed permission: {name}")


def _tree_sha256(root: Path, *, excluded: set[str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name not in excluded):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        if path.name == "stage.json" or relative == "artifact_hash_chain.json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            digest.update(json.dumps(_without_runtime_fields(payload), sort_keys=True, separators=(",", ":")).encode("utf-8"))
        else:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _without_runtime_fields(value):
    if isinstance(value, dict):
        return {
            key: _without_runtime_fields(item)
            for key, item in value.items()
            if key not in {"started_at", "completed_at"}
        }
    if isinstance(value, list):
        return [_without_runtime_fields(item) for item in value]
    return value


def _fsync_tree(root: Path) -> None:
    for path in (item for item in root.rglob("*") if item.is_file()):
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _make_tree_read_only(root: Path) -> None:
    for path in (item for item in root.rglob("*") if item.is_file()):
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        path.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    root.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

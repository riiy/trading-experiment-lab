from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from texperiment.audit.manifest import sha256_file
from texperiment.full_recalculation.immutability import RecalculationAbort
from texperiment.full_recalculation.stages import StageId


@dataclass(frozen=True)
class ArtifactRegistry:
    run_id: str
    temporary_root: Path
    final_root: Path
    failure_diagnostics_root: Path


@dataclass(frozen=True)
class RegisteredArtifact:
    name: str
    path: Path
    sha256: str
    producer: StageId


def build_artifact_registry(
    data_root: str | Path,
    diagnostics_root: str | Path,
    run_id: str,
) -> ArtifactRegistry:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be one non-empty path component")
    data = Path(data_root)
    diagnostics = Path(diagnostics_root)
    return ArtifactRegistry(
        run_id=run_id,
        temporary_root=data / "recalculations" / ".tmp" / run_id,
        final_root=data / "recalculations" / "STOCK_RS_PULLBACK_v1_RECALCULATED" / run_id,
        failure_diagnostics_root=diagnostics / "recalculation_attempts" / run_id,
    )


def register_artifact(
    artifacts: dict[str, RegisteredArtifact],
    *,
    name: str,
    path: str | Path,
    producer: StageId,
) -> RegisteredArtifact:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    artifact = RegisteredArtifact(name=name, path=source, sha256=sha256_file(source), producer=producer)
    existing = artifacts.get(name)
    if existing is not None and existing != artifact:
        raise ValueError(f"artifact already registered with different identity: {name}")
    artifacts[name] = artifact
    return artifact


def require_artifact(
    artifacts: dict[str, RegisteredArtifact],
    name: str,
    *,
    expected_producer: StageId | None = None,
) -> RegisteredArtifact:
    if name not in artifacts:
        raise KeyError(f"artifact is not registered: {name}")
    artifact = artifacts[name]
    if expected_producer is not None and artifact.producer != expected_producer:
        raise ValueError(f"artifact producer mismatch for {name}: {artifact.producer.value}")
    if not artifact.path.is_file() or sha256_file(artifact.path) != artifact.sha256:
        raise RecalculationAbort(
            "RECALCULATION_ABORTED_INPUT_DRIFT",
            f"registered artifact changed: {name}",
        )
    return artifact

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactRegistry:
    run_id: str
    temporary_root: Path
    final_root: Path
    failure_diagnostics_root: Path


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

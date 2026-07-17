from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def render_audit_report(manifest: dict[str, Any], samples: pd.DataFrame, details: pd.DataFrame, summary: dict[str, Any] | None = None) -> str:
    decision = "AUDIT_PENDING_MANUAL_REVIEW" if summary is None else summary["decision"]
    lines = [
        "# AUDIT_STOCK_RS_PULLBACK_v1",
        "",
        f"Decision: **{decision}**",
        "",
        "该审计不改变 `STOCK_RS_PULLBACK_v1 = FAILED_ARCHIVED`，不触发重算、账户仿真、交易票或新 Setup。",
        "",
        "## Frozen Inputs",
        "",
        f"- Plan: `{manifest.get('audit_plan_version')}`",
        f"- Git commit: `{manifest.get('git_commit')}`",
        f"- Git dirty: `{manifest.get('git_dirty')}`",
        f"- Sample count: `{len(samples)}`",
        "",
        "## Sample Categories",
        "",
    ]
    for category, count in samples["audit_category"].value_counts().sort_index().items():
        lines.append(f"- `{category}`: {count}")
    lines += ["", "## Automated Checks", ""]
    for verdict, count in details["verdict"].value_counts().sort_index().items():
        lines.append(f"- `{verdict}`: {count}")
    lines += ["", "Manual reviewer fields must be completed before a final audit decision is emitted.", ""]
    return "\n".join(lines)


def write_audit_outputs(
    output_dir: str | Path,
    *,
    manifest: dict[str, Any],
    samples: pd.DataFrame,
    details: pd.DataFrame | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "STOCK_RS_PULLBACK_v1_audit_manifest.json"
    samples_path = root / "STOCK_RS_PULLBACK_v1_audit_samples.csv"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    samples.to_csv(samples_path, index=False, encoding="utf-8-sig")
    paths = {"manifest": manifest_path, "samples": samples_path}
    if details is not None:
        details_path = root / "STOCK_RS_PULLBACK_v1_audit_details.csv"
        report_path = root / "AUDIT_STOCK_RS_PULLBACK_v1.md"
        details.to_csv(details_path, index=False, encoding="utf-8-sig")
        report_path.write_text(render_audit_report(manifest, samples, details, summary), encoding="utf-8")
        paths.update({"details": details_path, "report": report_path})
    if summary is not None:
        summary_path = root / "STOCK_RS_PULLBACK_v1_audit_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["summary"] = summary_path
    return paths

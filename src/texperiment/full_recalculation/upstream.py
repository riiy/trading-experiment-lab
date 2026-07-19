from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.audit.manifest import profile_table, sha256_file
from texperiment.config.loader import load_yaml
from texperiment.data.normalizer import normalize_daily_bars
from texperiment.data.tdx_paired_source import apply_daily_ratio_mapping, fit_raw_qfq_mapping
from texperiment.full_recalculation.artifact_registry import register_artifact, require_artifact
from texperiment.full_recalculation.immutability import (
    RecalculationAbort,
    assert_publish_target_absent,
    assert_repository_frozen,
)
from texperiment.full_recalculation.schema import validate_manifest_v2
from texperiment.full_recalculation.stages import StageContext, StageId, StageResult, StageStatus
from texperiment.indicators.a_share import AShareIndicatorConfig, write_a_share_indicators_from_parquet
from texperiment.market_rules.price_limit import enrich_price_limit_fields
from texperiment.universe.a_share import AShareUniverseConfig, write_a_share_universe_from_parquet

_TIMEZONE = ZoneInfo("Asia/Shanghai")
_MARKET_PROFILE_FIELDS = ("date", "code")


class InputSnapshotStage:
    stage_id = StageId.INPUT_SNAPSHOT

    def __init__(
        self,
        *,
        repository_state: Callable[[Path], tuple[str, bool]] | None = None,
        batch_size: int = 250_000,
    ):
        self.repository_state = repository_state or _repository_state
        self.batch_size = batch_size

    def run(self, context: StageContext) -> StageResult:
        started = _now()
        validate_manifest_v2(context.manifest)
        expected_commit = str(context.manifest["repository"]["commit"])
        current_commit, git_dirty = self.repository_state(context.project_root)
        assert_repository_frozen(
            current_commit=current_commit,
            expected_commit=expected_commit,
            git_dirty=git_dirty,
        )
        if context.final_root is None:
            raise ValueError("INPUT_SNAPSHOT requires context.final_root")
        assert_publish_target_absent(context.final_root)
        assert_publish_target_absent(context.work_root)

        input_hashes: dict[str, str] = {}
        profiles: dict[str, dict[str, Any]] = {}
        resolved: dict[str, Path] = {}
        for name, spec in context.manifest["inputs"].items():
            path = _resolve(context.project_root, spec["path"])
            if not path.is_file():
                raise FileNotFoundError(path)
            digest = sha256_file(path)
            if digest != spec["sha256"]:
                raise RecalculationAbort(
                    "RECALCULATION_ABORTED_INPUT_DRIFT",
                    f"input hash changed: {name}",
                )
            input_hashes[name] = digest
            resolved[name] = path
            if name in {"raw_daily", "qfq_daily", "benchmark"}:
                profile = profile_table(
                    path,
                    key_fields=_MARKET_PROFILE_FIELDS,
                    critical_fields=_MARKET_PROFILE_FIELDS,
                    batch_size=self.batch_size,
                )
                _assert_profile_matches(name, profile, spec)
                profiles[name] = profile

        _assert_table_keys_equal(resolved["raw_daily"], resolved["qfq_daily"], self.batch_size)
        context.work_root.mkdir(parents=True, exist_ok=False)
        snapshot_dir = context.work_root / self.stage_id.value
        snapshot_dir.mkdir()
        snapshot_path = snapshot_dir / "snapshot.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "contract": context.manifest["contract"],
                    "repository": {"commit": current_commit, "git_dirty": False},
                    "input_hashes": input_hashes,
                    "profiles": profiles,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        for name, path in resolved.items():
            register_artifact(
                context.artifacts,
                name=f"input.{name}",
                path=path,
                producer=self.stage_id,
            )
        snapshot = register_artifact(
            context.artifacts,
            name="snapshot.metadata",
            path=snapshot_path,
            producer=self.stage_id,
        )
        snapshot_outputs = {
            name: artifact.sha256
            for name, artifact in context.artifacts.items()
            if artifact.producer == self.stage_id
        }
        result = StageResult(
            stage=self.stage_id,
            status=StageStatus.PASSED,
            started_at=started,
            completed_at=_now(),
            input_hashes=input_hashes,
            output_hashes=snapshot_outputs,
            rows=profiles["raw_daily"]["rows"],
            min_date=profiles["raw_daily"]["min_date"],
            max_date=profiles["raw_daily"]["max_date"],
            unique_codes=profiles["raw_daily"]["code_count"],
        )
        _write_stage_record(context, result)
        return result


class MarketStateRebuildStage:
    stage_id = StageId.MARKET_STATE_REBUILD

    def __init__(self, *, batch_size: int = 250_000):
        self.batch_size = batch_size

    def run(self, context: StageContext) -> StageResult:
        started = _now()
        raw = require_artifact(context.artifacts, "input.raw_daily", expected_producer=StageId.INPUT_SNAPSHOT)
        qfq = require_artifact(context.artifacts, "input.qfq_daily", expected_producer=StageId.INPUT_SNAPSHOT)
        policy = require_artifact(context.artifacts, "input.st_overrides", expected_producer=StageId.INPUT_SNAPSHOT)
        stage_dir = context.work_root / self.stage_id.value
        stage_dir.mkdir(exist_ok=False)
        output = stage_dir / "market_state.parquet"
        diagnostics = rebuild_market_state(
            raw.path,
            qfq.path,
            policy.path,
            output,
            batch_size=self.batch_size,
        )
        artifact = register_artifact(
            context.artifacts,
            name="market_state.daily",
            path=output,
            producer=self.stage_id,
        )
        profile = profile_table(
            output,
            key_fields=_MARKET_PROFILE_FIELDS,
            critical_fields=("date", "code", "raw_open", "adj_open", "mapping_status"),
            batch_size=self.batch_size,
        )
        warnings = ()
        if diagnostics["mapping_unknown_rows"]:
            warnings = (f"mapping_unknown_rows={diagnostics['mapping_unknown_rows']}",)
        result = StageResult(
            stage=self.stage_id,
            status=StageStatus.PASSED,
            started_at=started,
            completed_at=_now(),
            input_hashes={raw.name: raw.sha256, qfq.name: qfq.sha256, policy.name: policy.sha256},
            output_hashes={artifact.name: artifact.sha256},
            rows=profile["rows"],
            min_date=profile["min_date"],
            max_date=profile["max_date"],
            unique_codes=profile["code_count"],
            warnings=warnings,
        )
        _write_stage_record(context, result, extra={"market_state": diagnostics})
        return result


class UniverseRebuildStage:
    stage_id = StageId.UNIVERSE_REBUILD

    def __init__(self, *, batch_size: int = 250_000):
        self.batch_size = batch_size

    def run(self, context: StageContext) -> StageResult:
        started = _now()
        market = require_artifact(
            context.artifacts,
            "market_state.daily",
            expected_producer=StageId.MARKET_STATE_REBUILD,
        )
        setup_artifact = require_artifact(
            context.artifacts,
            "input.setup_config",
            expected_producer=StageId.INPUT_SNAPSHOT,
        )
        setup = load_yaml(setup_artifact.path)
        stage_dir = context.work_root / self.stage_id.value
        stage_dir.mkdir(exist_ok=False)
        output = stage_dir / "universe.parquet"
        rows, eligible = write_a_share_universe_from_parquet(
            market.path,
            output,
            config=AShareUniverseConfig.from_setup_config(setup),
            include_rejected=True,
            batch_size=self.batch_size,
        )
        artifact = register_artifact(
            context.artifacts,
            name="universe.daily",
            path=output,
            producer=self.stage_id,
        )
        profile = profile_table(
            output,
            key_fields=_MARKET_PROFILE_FIELDS,
            critical_fields=(
                "date",
                "code",
                "is_tradable_universe",
                "reject_reasons",
                "pass_st",
                "pass_execution_state",
            ),
            batch_size=self.batch_size,
        )
        if rows != profile["rows"]:
            raise ValueError("Universe row count changed while profiling")
        result = StageResult(
            stage=self.stage_id,
            status=StageStatus.PASSED,
            started_at=started,
            completed_at=_now(),
            input_hashes={market.name: market.sha256, setup_artifact.name: setup_artifact.sha256},
            output_hashes={artifact.name: artifact.sha256},
            rows=rows,
            min_date=profile["min_date"],
            max_date=profile["max_date"],
            unique_codes=profile["code_count"],
            warnings=(f"eligible_rows={eligible}",),
        )
        _write_stage_record(context, result)
        return result


class IndicatorRebuildStage:
    stage_id = StageId.INDICATOR_REBUILD

    def __init__(self, *, batch_size: int = 250_000):
        self.batch_size = batch_size

    def run(self, context: StageContext) -> StageResult:
        started = _now()
        qfq = require_artifact(context.artifacts, "input.qfq_daily", expected_producer=StageId.INPUT_SNAPSHOT)
        benchmark = require_artifact(context.artifacts, "input.benchmark", expected_producer=StageId.INPUT_SNAPSHOT)
        setup_artifact = require_artifact(
            context.artifacts,
            "input.setup_config",
            expected_producer=StageId.INPUT_SNAPSHOT,
        )
        universe = require_artifact(
            context.artifacts,
            "universe.daily",
            expected_producer=StageId.UNIVERSE_REBUILD,
        )
        setup = load_yaml(setup_artifact.path)
        stage_dir = context.work_root / self.stage_id.value
        stage_dir.mkdir(exist_ok=False)
        output = stage_dir / "indicators.parquet"
        benchmark_frame = _prepare_benchmark_for_indicators(_read_table(benchmark.path))
        rows, complete = write_a_share_indicators_from_parquet(
            qfq.path,
            output,
            benchmark_bars=benchmark_frame,
            config=AShareIndicatorConfig.from_setup_config(setup),
            batch_size=self.batch_size,
        )
        indicator_artifact = register_artifact(
            context.artifacts,
            name="indicators.daily",
            path=output,
            producer=self.stage_id,
        )
        sample_path = stage_dir / "eligible_indicator_sample.parquet"
        sample_rows = write_eligible_indicator_sample(
            output,
            universe.path,
            sample_path,
            batch_size=self.batch_size,
        )
        sample_artifact = register_artifact(
            context.artifacts,
            name="indicators.eligible_sample",
            path=sample_path,
            producer=self.stage_id,
        )
        profile = profile_table(
            output,
            key_fields=_MARKET_PROFILE_FIELDS,
            critical_fields=("date", "code", "ma20", "ma60", "ret20", "benchmark_ret20"),
            batch_size=self.batch_size,
        )
        if rows != profile["rows"]:
            raise ValueError("indicator row count changed while profiling")
        result = StageResult(
            stage=self.stage_id,
            status=StageStatus.PASSED,
            started_at=started,
            completed_at=_now(),
            input_hashes={
                qfq.name: qfq.sha256,
                benchmark.name: benchmark.sha256,
                setup_artifact.name: setup_artifact.sha256,
                universe.name: universe.sha256,
            },
            output_hashes={
                indicator_artifact.name: indicator_artifact.sha256,
                sample_artifact.name: sample_artifact.sha256,
            },
            rows=rows,
            min_date=profile["min_date"],
            max_date=profile["max_date"],
            unique_codes=profile["code_count"],
            warnings=(f"complete_indicator_rows={complete}", f"eligible_sample_rows={sample_rows}"),
        )
        _write_stage_record(context, result)
        return result


def rebuild_market_state(
    raw_path: str | Path,
    qfq_path: str | Path,
    policy_path: str | Path,
    output_path: str | Path,
    *,
    batch_size: int = 250_000,
) -> dict[str, int]:
    st_overrides = _load_market_policy(Path(policy_path))
    raw_file = pq.ParquetFile(raw_path)
    qfq_file = pq.ParquetFile(qfq_path)
    output = Path(output_path)
    temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    rows = mapping_unknown = branch_invariant = st_blocking = ratio_mapped = 0
    matched_overrides: set[tuple[str, str]] = set()
    try:
        raw_batches = raw_file.iter_batches(batch_size=batch_size)
        qfq_batches = qfq_file.iter_batches(batch_size=batch_size)
        for raw_batch, qfq_batch in zip_longest(raw_batches, qfq_batches):
            if raw_batch is None or qfq_batch is None:
                raise ValueError("raw/qfq row counts differ")
            raw = raw_batch.to_pandas()
            qfq = qfq_batch.to_pandas()
            _assert_frame_keys_equal(raw, qfq)
            frame = _combine_raw_qfq(raw, qfq)
            frame = fit_raw_qfq_mapping(frame)
            before = frame["adjustment_status"].copy()
            frame = apply_daily_ratio_mapping(frame)
            ratio_mapped += int((before.ne(frame["adjustment_status"])).sum())
            for key, status in st_overrides.items():
                selected = frame["code"].astype(str).eq(key[0]) & frame["date"].eq(pd.Timestamp(key[1]))
                if selected.any():
                    frame.loc[selected, "historical_st_status"] = status
                    matched_overrides.add(key)
            frame = enrich_price_limit_fields(frame)
            frame["raw_qfq_ratio"] = frame["adj_close"] / frame["raw_close"]
            frame["mapping_status"] = frame["adjustment_status"]
            frame["st_branch_status"] = frame["historical_st_branch_status"]
            mapping_unknown += int(frame["mapping_status"].astype(str).str.startswith("UNKNOWN").sum())
            branch_invariant += int(frame["st_branch_status"].eq("PASS_BRANCH_INVARIANT").sum())
            st_blocking += int(frame["st_branch_status"].eq("NOT_EVALUABLE_MISSING_HISTORICAL_ST").sum())
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp, table.schema, compression="snappy")
            else:
                table = table.cast(writer.schema, safe=False)
            writer.write_table(table)
            rows += len(frame)
        if writer is None:
            raise ValueError("raw/qfq inputs contained no rows")
        missing_overrides = sorted(set(st_overrides) - matched_overrides)
        if missing_overrides:
            raise ValueError(f"historical ST overrides did not match: {missing_overrides}")
        writer.close()
        writer = None
        os.replace(temp, output)
    finally:
        if writer is not None:
            writer.close()
        if temp.exists():
            temp.unlink()
    return {
        "rows": rows,
        "mapping_unknown_rows": mapping_unknown,
        "daily_ratio_mapping_rows": ratio_mapped,
        "st_branch_invariant_rows": branch_invariant,
        "material_st_blocking_rows": st_blocking,
    }


def write_eligible_indicator_sample(
    indicator_path: str | Path,
    universe_path: str | Path,
    output_path: str | Path,
    *,
    batch_size: int,
) -> int:
    indicators = pq.ParquetFile(indicator_path)
    universe = pq.ParquetFile(universe_path)
    output = Path(output_path)
    writer: pq.ParquetWriter | None = None
    rows = 0
    schema = indicators.schema_arrow
    try:
        pairs = zip_longest(
            indicators.iter_batches(batch_size=batch_size),
            universe.iter_batches(batch_size=batch_size),
        )
        for indicator_batch, universe_batch in pairs:
            if indicator_batch is None or universe_batch is None:
                raise ValueError("indicator/Universe row counts differ")
            indicator_frame = indicator_batch.to_pandas()
            universe_frame = universe_batch.to_pandas()
            _assert_frame_keys_equal(indicator_frame, universe_frame)
            selected = indicator_frame.loc[universe_frame["is_tradable_universe"].astype(bool)].copy()
            if selected.empty:
                continue
            table = pa.Table.from_pandas(selected, schema=schema, preserve_index=False, safe=False)
            if writer is None:
                writer = pq.ParquetWriter(output, schema, compression="snappy")
            writer.write_table(table)
            rows += len(selected)
        if writer is None:
            pq.write_table(pa.Table.from_batches([], schema=schema), output, compression="snappy")
        return rows
    finally:
        if writer is not None:
            writer.close()


def _combine_raw_qfq(raw: pd.DataFrame, qfq: pd.DataFrame) -> pd.DataFrame:
    base = qfq.copy()
    for field in ("open", "high", "low", "close"):
        base[field] = _series(qfq, (f"adj_{field}", field))
        base[f"adj_{field}"] = base[field]
        base[f"raw_{field}"] = _series(raw, (f"raw_{field}", field))
    base["pre_close"] = _series(qfq, ("pre_close", "adj_pre_close"), required=False)
    base["raw_pre_close"] = _series(raw, ("raw_pre_close", "pre_close"), required=False)
    for field in ("volume", "amount"):
        base[field] = _series(raw, (field,), required=True)
    metadata = (
        "name",
        "board",
        "listing_date",
        "listing_date_status",
        "listing_trading_day",
        "historical_st_status",
        "opening_auction_fill_status",
        "closing_auction_fill_status",
        "trade_status",
        "is_suspended",
        "industry",
    )
    for field in metadata:
        if field in raw:
            base[field] = raw[field].to_numpy()
    base["adj_type"] = "qfq"
    normalized = normalize_daily_bars(
        base,
        provider="canonical",
        adj_type="qfq",
        source="full_pipeline_raw_qfq_rebuild",
    )
    return normalized


def _load_market_policy(path: Path) -> dict[tuple[str, str], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("historical_st_point_overrides", payload.get("st_overrides", []))
    overrides = {
        (str(item["code"]), pd.Timestamp(item["date"]).date().isoformat()): str(item["status"]).upper()
        for item in rows
    }
    return overrides


def _assert_profile_matches(name: str, actual: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    comparisons = {
        "rows": actual["rows"],
        "min_date": actual["min_date"],
        "max_date": actual["max_date"],
        "codes": actual["code_count"],
    }
    drift = {field: (value, expected[field]) for field, value in comparisons.items() if value != expected[field]}
    if drift:
        raise RecalculationAbort(
            "RECALCULATION_ABORTED_INPUT_DRIFT",
            f"input profile changed for {name}: {drift}",
        )
    actual_adj_types = actual.get("adj_type_values", [])
    actual_sources = actual.get("source_values", [])
    if actual_adj_types and actual_adj_types != [expected["adj_type"]]:
        raise RecalculationAbort(
            "RECALCULATION_ABORTED_INPUT_DRIFT",
            f"input adjustment type changed for {name}: {actual_adj_types}",
        )
    if actual_sources and actual_sources != [expected["source"]]:
        raise RecalculationAbort(
            "RECALCULATION_ABORTED_INPUT_DRIFT",
            f"input source changed for {name}: {actual_sources}",
        )


def _assert_table_keys_equal(left_path: Path, right_path: Path, batch_size: int) -> None:
    left = pq.ParquetFile(left_path).iter_batches(batch_size=batch_size, columns=["date", "code"])
    right = pq.ParquetFile(right_path).iter_batches(batch_size=batch_size, columns=["date", "code"])
    for left_batch, right_batch in zip_longest(left, right):
        if left_batch is None or right_batch is None:
            raise RecalculationAbort(
                "RECALCULATION_ABORTED_PIPELINE_CONTRACT_MISMATCH",
                "raw/qfq row counts differ",
            )
        try:
            _assert_frame_keys_equal(left_batch.to_pandas(), right_batch.to_pandas())
        except ValueError as exc:
            raise RecalculationAbort(
                "RECALCULATION_ABORTED_PIPELINE_CONTRACT_MISMATCH",
                "raw/qfq primary keys differ",
            ) from exc


def _assert_frame_keys_equal(left: pd.DataFrame, right: pd.DataFrame) -> None:
    if len(left) != len(right):
        raise ValueError("aligned frames have different row counts")
    left_keys = left[["date", "code"]].copy().reset_index(drop=True)
    right_keys = right[["date", "code"]].copy().reset_index(drop=True)
    for frame in (left_keys, right_keys):
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame["code"] = frame["code"].astype(str)
    if not left_keys.equals(right_keys):
        raise ValueError("aligned frame keys differ")


def _series(frame: pd.DataFrame, candidates: tuple[str, ...], *, required: bool = True) -> pd.Series:
    for candidate in candidates:
        if candidate in frame:
            return frame[candidate].reset_index(drop=True)
    if required:
        raise ValueError(f"missing input columns: {candidates}")
    return pd.Series(pd.NA, index=range(len(frame)))


def _write_stage_record(context: StageContext, result: StageResult, *, extra: Mapping[str, Any] | None = None) -> None:
    payload = asdict(result)
    payload["stage"] = result.stage.value
    payload["status"] = result.status.value
    payload["blocking_errors"] = list(result.blocking_errors)
    payload["warnings"] = list(result.warnings)
    payload["stage"] = {
        "id": result.stage.value,
        "sequence": list(StageId).index(result.stage) + 1,
        "status": result.status.value,
    }
    payload["inputs"] = [
        _artifact_record(context, artifact_id, digest, output=False)
        for artifact_id, digest in result.input_hashes.items()
    ]
    payload["outputs"] = [
        _artifact_record(context, artifact_id, digest, output=True, result=result)
        for artifact_id, digest in result.output_hashes.items()
    ]
    payload.pop("input_hashes", None)
    payload.pop("output_hashes", None)
    payload.pop("status", None)
    if extra:
        payload.update(extra)
    path = context.work_root / result.stage.value / "stage.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _artifact_record(
    context: StageContext,
    artifact_id: str,
    digest: str,
    *,
    output: bool,
    result: StageResult | None = None,
) -> dict[str, Any]:
    artifact = context.artifacts.get(artifact_id)
    if artifact is None and not artifact_id.startswith("input."):
        artifact = context.artifacts.get(f"input.{artifact_id}")
    if artifact is None:
        raise ValueError(f"stage record references unregistered artifact: {artifact_id}")
    if artifact.sha256 != digest:
        raise ValueError(f"stage record hash differs from registry: {artifact_id}")
    try:
        path = artifact.path.relative_to(context.work_root)
    except ValueError:
        try:
            path = artifact.path.relative_to(context.project_root)
        except ValueError:
            path = artifact.path
    record: dict[str, Any] = {
        "artifact_id": artifact.name,
        "producer_stage": artifact.producer.value if artifact.producer is not None else None,
        "source_class": artifact.source_class,
        "registered_by_stage": (
            artifact.registered_by_stage.value if artifact.registered_by_stage is not None else None
        ),
        "allowed_consumers": [stage.value for stage in artifact.allowed_consumers],
        "path": str(path),
    }
    if output:
        record.update({
            "sha256": digest,
            "rows": result.rows if result else 0,
            "min_date": result.min_date if result else None,
            "max_date": result.max_date if result else None,
        })
    else:
        record.update({"expected_sha256": digest, "verified_sha256": digest})
    return record


def _repository_state(root: Path) -> tuple[str, bool]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())
    return commit, dirty


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _read_table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)


def _prepare_benchmark_for_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for field in ("open", "high", "low", "close"):
        adjusted = f"adj_{field}"
        if adjusted not in out:
            if field not in out:
                raise ValueError(f"benchmark missing price column: {field}")
            out[adjusted] = out[field]
    return out


def _now() -> str:
    return datetime.now(_TIMEZONE).isoformat()

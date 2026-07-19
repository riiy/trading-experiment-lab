from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from texperiment.config.loader import load_yaml
from texperiment.config.validator import validate_global_account_config, validate_setup_config
from texperiment.backtest.engine import (
    run_stock_rs_pullback_backtest,
    run_stock_rs_pullback_backtest_from_parquet,
    summarize_backtest_trades,
    write_trades,
)
from texperiment.account.account_simulator import (
    build_account_simulation_artifacts,
    write_account_simulation_outputs,
)
from texperiment.audit.manifest import build_audit_manifest
from texperiment.audit.report import write_audit_outputs
from texperiment.audit.sampler import select_audit_sample
from texperiment.data.loaders import ingest_a_share_daily, read_daily_bars, read_table, write_parquet
from texperiment.data.quality import validate_daily_bars
from texperiment.data.tdx_export_source import write_tdx_index_parquet
from texperiment.data.tdx_paired_source import write_tdx_paired_export_parquet
from texperiment.data.core_input_pair import CoreInputPairError, prepare_tdx_core_input_pair
from texperiment.indicators.a_share import (
    AShareIndicatorConfig,
    build_a_share_indicators,
    write_a_share_indicators_from_parquet,
    write_indicators,
)
from texperiment.full_recalculation.formal_cli import (
    freeze_v2_from_args,
    run_v2_from_args,
    validate_v2_from_args,
)
from texperiment.guards.trading_permission import assert_trading_disabled
from texperiment.guards.setup_status import is_archived
from texperiment.metrics.validation import build_validation_artifacts, write_validation_outputs
from texperiment.recalculation import (
    build_recalculation_manifest,
    run_full_recalculation,
    write_recalculation_manifest,
)
from texperiment.tickets.generator import build_trade_ticket_artifacts, write_ticket_outputs
from texperiment.setups.stock_rs_pullback_v1.signal import (
    build_stock_rs_pullback_signals,
    build_stock_rs_pullback_signals_from_parquet,
    validate_universe_coverage,
    write_signals,
)
from texperiment.universe.a_share import (
    AShareUniverseConfig,
    build_a_share_universe,
    write_a_share_universe_from_parquet,
    write_universe,
)

ROOT = Path(__file__).resolve().parents[2]


def cmd_config_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    account = load_yaml(root / "configs" / "global_account.yaml")
    registry = load_yaml(root / "experiment_registry.yaml")
    current_setup = registry.get("Trading_Experiment", {}).get("current_setup")
    setup_ids = list(_registered_setups(registry))

    validate_global_account_config(account)
    for setup_id in setup_ids:
        validate_setup_config(load_yaml(root / "configs" / "setups" / f"{setup_id}.yaml"))
    assert_trading_disabled(registry)

    print("config-check: OK")
    print("trading_allowed: false")
    print(f"current_setup: {current_setup if current_setup is not None else 'null'}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    registry = load_yaml(root / "experiment_registry.yaml")
    exp = registry.get("Trading_Experiment", {})
    print(f"status: {exp.get('status')}")
    current_setup = exp.get("current_setup")
    print(f"current_setup: {current_setup if current_setup is not None else 'null'}")
    print(f"trading_allowed: {exp.get('trading_allowed')}")
    return 0


def cmd_ingest_a_share_daily(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    input_path = _resolve(root, args.input)
    output_path = _resolve(root, args.output)
    df = ingest_a_share_daily(
        input_path,
        provider=args.provider,
        adj_type=args.adj_type,
        source=args.source,
    )
    report = validate_daily_bars(df, strict=not args.allow_quality_warnings)
    write_parquet(df, output_path)

    print(f"ingest-a-share-daily: OK -> {output_path}")
    print(_quality_report_to_json(report))
    return 0


def cmd_data_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    path = _resolve(root, args.path)
    df = read_daily_bars(path)
    report = validate_daily_bars(df, strict=not args.allow_quality_warnings)
    print("data-check: OK" if report.ok else "data-check: WARN")
    print(_quality_report_to_json(report))
    return 0


def cmd_ingest_tdx_export_index_daily(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    output_path = _resolve(root, args.output)
    report = write_tdx_index_parquet(_resolve(root, args.input), output_path, code=args.code)
    print(f"ingest-tdx-export-index-daily: OK -> {output_path}")
    print(_quality_report_to_json(report))
    return 0


def cmd_ingest_tdx_paired_a_share_daily(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    output_path = _resolve(root, args.output)
    quality, report = write_tdx_paired_export_parquet(
        _resolve(root, args.qfq_input),
        _resolve(root, args.raw_input),
        _resolve(root, args.hfq_input),
        output_path,
        strict=not args.allow_quality_warnings,
    )
    print(f"ingest-tdx-paired-a-share-daily: OK -> {output_path}")
    print(json.dumps({"quality": json.loads(_quality_report_to_json(quality)), "paired": report.__dict__}, ensure_ascii=False, indent=2))
    return 0


def cmd_prepare_stock_rs_pullback_core_input_pair(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        result = prepare_tdx_core_input_pair(
            _resolve(root, args.raw_input),
            _resolve(root, args.qfq_input),
            _resolve(root, args.output_root),
            hfq_input=_resolve(root, args.hfq_input) if args.hfq_input else None,
            diagnostics_path=_resolve(root, args.diagnostics),
        )
    except CoreInputPairError as exc:
        raise SystemExit(f"CORE_INPUT_PAIR_VALIDATION_FAILED: {exc}") from exc
    print(f"prepare-stock-rs-pullback-core-input-pair: OK -> {result.output_root}")
    print(json.dumps(result.report, ensure_ascii=False, indent=2))
    return 0


def cmd_build_a_share_universe(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    input_path = _resolve(root, args.input)
    output_path = _resolve(root, args.output)
    setup_path = root / "configs" / "setups" / f"{args.setup}.yaml"
    setup_config = load_yaml(setup_path)
    universe_config = AShareUniverseConfig.from_setup_config(setup_config)

    if input_path.suffix.lower() == ".parquet":
        rows_written, eligible_count = write_a_share_universe_from_parquet(
            input_path,
            output_path,
            as_of_date=args.as_of,
            config=universe_config,
            include_rejected=args.include_rejected,
            batch_size=args.batch_size,
        )
        print(f"build-a-share-universe: OK -> {output_path}")
        print(json.dumps({
            "setup": args.setup,
            "as_of": args.as_of,
            "rows_written": rows_written,
            "eligible_count": eligible_count,
            "include_rejected": bool(args.include_rejected),
            "min_avg_amount_20d": universe_config.min_avg_amount_20d,
            "max_one_lot_value": universe_config.max_one_lot_value,
            "min_listing_days": universe_config.min_listing_days,
        }, ensure_ascii=False, indent=2))
        if rows_written == 0 and not args.allow_empty:
            raise SystemExit(
                "build-a-share-universe produced 0 rows. "
                "Use --include-rejected to inspect rejection reasons or --allow-empty to suppress this error."
            )
        return 0

    df = read_daily_bars(input_path)
    universe = build_a_share_universe(
        df,
        as_of_date=args.as_of,
        config=universe_config,
        include_rejected=args.include_rejected,
    )
    if universe.empty and not args.allow_empty:
        raise SystemExit(
            "build-a-share-universe produced 0 rows. "
            "Use --include-rejected to inspect rejection reasons or --allow-empty to suppress this error."
        )

    write_universe(universe, output_path)
    eligible_count = int(universe["is_tradable_universe"].sum()) if "is_tradable_universe" in universe.columns else len(universe)
    print(f"build-a-share-universe: OK -> {output_path}")
    print(json.dumps({
        "setup": args.setup,
        "as_of": args.as_of,
        "rows_written": int(len(universe)),
        "eligible_count": eligible_count,
        "include_rejected": bool(args.include_rejected),
        "min_avg_amount_20d": universe_config.min_avg_amount_20d,
        "max_one_lot_value": universe_config.max_one_lot_value,
        "min_listing_days": universe_config.min_listing_days,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_compute_a_share_indicators(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    daily_path = _resolve(root, args.daily_input)
    output_path = _resolve(root, args.output)
    setup_path = root / "configs" / "setups" / f"{args.setup}.yaml"
    setup_config = load_yaml(setup_path)
    config = AShareIndicatorConfig.from_setup_config(setup_config)
    if args.benchmark_code:
        config = AShareIndicatorConfig(
            ma_short_window=config.ma_short_window,
            ma_long_window=config.ma_long_window,
            return_window=config.return_window,
            benchmark_code=args.benchmark_code,
            high_lookback_window=config.high_lookback_window,
            volume_ma_window=config.volume_ma_window,
        )

    if args.benchmark_input:
        benchmark = read_daily_bars(_resolve(root, args.benchmark_input))
    else:
        benchmark = read_daily_bars(daily_path)

    if daily_path.suffix.lower() == ".parquet":
        rows_written, complete_count = write_a_share_indicators_from_parquet(
            daily_path,
            output_path,
            benchmark_bars=benchmark,
            config=config,
            batch_size=args.batch_size,
        )
    else:
        daily = read_daily_bars(daily_path)
        indicators = build_a_share_indicators(daily, benchmark_bars=benchmark, config=config)
        write_indicators(indicators, output_path)
        rows_written = len(indicators)
        complete_count = int(indicators["has_complete_indicator_window"].sum()) if "has_complete_indicator_window" in indicators.columns else 0
    print(f"compute-a-share-indicators: OK -> {output_path}")
    print(json.dumps({
        "setup": args.setup,
        "rows_written": int(rows_written),
        "complete_indicator_rows": complete_count,
        "benchmark_code": config.benchmark_code,
        "ma_short_window": config.ma_short_window,
        "ma_long_window": config.ma_long_window,
        "return_window": config.return_window,
        "high_lookback_window": config.high_lookback_window,
        "volume_ma_window": config.volume_ma_window,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_generate_stock_rs_pullback_signals(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    _assert_full_recalculation_allowed(root)
    _assert_recalculated_paths(args.output)
    indicator_path = _resolve(root, args.indicator_input)
    output_path = _resolve(root, args.output)
    setup_path = root / "configs" / "setups" / f"{args.setup}.yaml"
    setup_config = load_yaml(setup_path)
    universe = None
    if args.universe_input and indicator_path.suffix.lower() != ".parquet":
        universe = read_daily_bars(_resolve(root, args.universe_input))
    if args.require_universe and not args.universe_input:
        raise SystemExit("--require-universe requires --universe-input")

    if indicator_path.suffix.lower() == ".parquet":
        try:
            signals = build_stock_rs_pullback_signals_from_parquet(
                indicator_path,
                universe_path=_resolve(root, args.universe_input) if args.universe_input else None,
                setup_config=setup_config,
                include_candidates=args.include_candidates,
                require_universe=args.require_universe,
                batch_size=args.batch_size,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        indicators = read_daily_bars(indicator_path)
        if args.universe_input:
            universe = read_daily_bars(_resolve(root, args.universe_input))
        if args.require_universe:
            try:
                validate_universe_coverage(indicators, universe)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
        signals = build_stock_rs_pullback_signals(
            indicators,
            universe=universe,
            setup_config=setup_config,
            include_candidates=args.include_candidates,
        )
    if signals.empty and not args.allow_empty:
        raise SystemExit(
            "generate-stock-rs-pullback-signals produced 0 rows. "
            "Use --include-candidates or --allow-empty to inspect/suppress empty output."
        )
    write_signals(signals, output_path)
    counts = signals["status"].value_counts().to_dict() if not signals.empty else {}
    print(f"generate-stock-rs-pullback-signals: OK -> {output_path}")
    print(json.dumps({
        "setup": args.setup,
        "rows_written": int(len(signals)),
        "status_counts": counts,
        "include_candidates": bool(args.include_candidates),
        "require_universe": bool(args.require_universe),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_backtest_stock_rs_pullback(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    _assert_full_recalculation_allowed(root)
    _assert_recalculated_paths(args.signal_input, args.output)
    setup_path = root / "configs" / "setups" / f"{args.setup}.yaml"
    setup_config = load_yaml(setup_path)
    signals = read_daily_bars(_resolve(root, args.signal_input))
    daily_path = _resolve(root, args.daily_input)
    if daily_path.suffix.lower() == ".parquet":
        trades = run_stock_rs_pullback_backtest_from_parquet(
            signals,
            daily_path,
            setup_config=setup_config,
            batch_size=args.batch_size,
        )
    else:
        daily = read_daily_bars(daily_path)
        trades = run_stock_rs_pullback_backtest(signals, daily, setup_config=setup_config)
    if trades.empty and not args.allow_empty:
        raise SystemExit(
            "backtest-stock-rs-pullback produced 0 trades; use --allow-empty to write empty output"
        )
    output_path = _resolve(root, args.output)
    write_trades(trades, output_path)
    print(f"backtest-stock-rs-pullback: OK -> {output_path}")
    print(json.dumps(summarize_backtest_trades(trades), ensure_ascii=False, indent=2))
    return 0


def cmd_report_stock_rs_pullback(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    _assert_full_recalculation_allowed(root)
    _assert_recalculated_paths(
        args.trade_input,
        args.metrics_output,
        args.report_output,
        args.yearly_output,
        args.industry_output,
    )
    setup_path = root / "configs" / "setups" / f"{args.setup}.yaml"
    setup_config = load_yaml(setup_path)
    trades = read_table(_resolve(root, args.trade_input))
    metadata = None
    if args.metadata_input:
        metadata = _read_metrics_metadata(_resolve(root, args.metadata_input), trades, args.batch_size)
    artifacts = build_validation_artifacts(
        trades,
        setup_config=setup_config,
        metadata=metadata,
    )
    if trades.empty and not args.allow_empty:
        raise SystemExit("report-stock-rs-pullback received empty trades; use --allow-empty to continue")
    output_paths = write_validation_outputs(
        artifacts,
        metrics_path=_resolve(root, args.metrics_output),
        report_path=_resolve(root, args.report_output),
        yearly_path=_resolve(root, args.yearly_output),
        industry_path=_resolve(root, args.industry_output),
    )
    print("report-stock-rs-pullback: OK")
    print(json.dumps({
        "decision": artifacts["metrics"]["decision"],
        "valid_trades": artifacts["metrics"]["overall"]["valid_trades"],
        "output_paths": {key: str(path) for key, path in output_paths.items()},
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_account_sim_stock_rs_pullback(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    setup_config = load_yaml(root / "configs" / "setups" / f"{args.setup}.yaml")
    account_config = load_yaml(root / "configs" / "global_account.yaml")
    _assert_setup_action_allowed(root, args.setup, "account simulation")
    metrics_path = _resolve(root, args.metrics_input)
    if not args.force_research:
        if not metrics_path.exists():
            raise SystemExit(
                f"validation metrics not found: {metrics_path}; use --force-research only for development"
            )
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        decision = metrics.get("decision")
        if decision != "VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION":
            raise SystemExit(
                f"account simulation requires validation PASS, got {decision!r}; "
                "use --force-research only for development"
            )

    trades = read_table(_resolve(root, args.trade_input))
    artifacts = build_account_simulation_artifacts(
        trades,
        account_config=account_config,
        setup_config=setup_config,
    )
    artifacts["summary"]["force_research"] = bool(args.force_research)
    paths = write_account_simulation_outputs(
        artifacts,
        simulation_path=_resolve(root, args.output),
        summary_path=_resolve(root, args.summary_output),
        report_path=_resolve(root, args.report_output),
    )
    summary = artifacts["summary"]
    print("account-sim-stock-rs-pullback: OK")
    print(json.dumps({
        "decision": summary.get("decision"),
        "accepted_trades": summary.get("accepted_trades"),
        "rejected_or_skipped": summary.get("rejected_or_skipped"),
        "force_research": summary["force_research"],
        "output_paths": {key: str(path) for key, path in paths.items()},
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_generate_stock_rs_pullback_tickets(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    setup_config = load_yaml(root / "configs" / "setups" / f"{args.setup}.yaml")
    account_config = load_yaml(root / "configs" / "global_account.yaml")
    _assert_setup_action_allowed(root, args.setup, "formal ticket generation")
    summary_path = _resolve(root, args.summary_input)
    if not summary_path.exists():
        raise SystemExit(f"account simulation summary not found: {summary_path}")
    account_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if account_summary.get("force_research"):
        raise SystemExit("formal tickets are blocked for force-research account simulation output")

    account_sim = read_table(_resolve(root, args.account_sim_input))
    try:
        artifacts = build_trade_ticket_artifacts(
            account_sim,
            account_config=account_config,
            setup_config=setup_config,
            account_summary=account_summary,
            selected_trade_id=args.trade_id,
            selected_simulation_id=args.simulation_id,
        )
    except PermissionError as exc:
        raise SystemExit(str(exc)) from exc
    paths = write_ticket_outputs(
        artifacts,
        output_dir=_resolve(root, args.output_dir),
        index_path=_resolve(root, args.index_output),
        summary_path=_resolve(root, args.summary_output),
        report_path=_resolve(root, args.report_output),
    )
    print("generate-stock-rs-pullback-tickets: OK")
    print(json.dumps({
        "decision": artifacts["summary"]["decision"],
        "tickets_generated": artifacts["summary"]["tickets_generated"],
        "tickets_rejected": artifacts["summary"]["tickets_rejected"],
        "output_paths": {
            "index": str(paths["index"]),
            "summary": str(paths["summary"]),
            "report": str(paths["report"]),
        },
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_prepare_stock_rs_pullback_audit(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    inputs = {
        "configs/setups/STOCK_RS_PULLBACK_v1.yaml": {
            "key_fields": (),
            "critical_fields": (),
        },
        args.daily_input: {
            "key_fields": ("date", "code"),
            "critical_fields": ("date", "code", "open", "high", "low", "close", "volume", "amount"),
        },
        args.indicator_input: {
            "key_fields": ("date", "code"),
            "critical_fields": ("date", "code", "ma20", "ma60", "ret20", "benchmark_ret20"),
        },
        args.universe_input: {
            "key_fields": ("date", "code"),
            "critical_fields": ("date", "code", "is_tradable_universe"),
        },
        args.signal_input: {
            "key_fields": ("signal_id",),
            "critical_fields": ("signal_id", "code", "signal_date", "status"),
        },
        args.trade_input: {
            "key_fields": ("trade_id",),
            "critical_fields": ("trade_id", "signal_id", "code", "status"),
        },
    }
    manifest = build_audit_manifest(root, inputs, batch_size=args.batch_size)
    trades = read_table(_resolve(root, args.trade_input))
    samples = select_audit_sample(trades)
    paths = write_audit_outputs(_resolve(root, args.output_dir), manifest=manifest, samples=samples)
    print("prepare-stock-rs-pullback-audit: OK")
    print(json.dumps({"sample_count": len(samples), "output_paths": {key: str(path) for key, path in paths.items()}}, ensure_ascii=False, indent=2))
    return 0


def cmd_freeze_stock_rs_pullback_recalculation(args: argparse.Namespace) -> int:
    raise SystemExit(
        "legacy freeze command is SIGNAL_EXECUTION_REPLAY only and cannot create a formal full-pipeline Manifest"
    )


def cmd_run_stock_rs_pullback_recalculation(args: argparse.Namespace) -> int:
    raise SystemExit(
        "legacy run command is SIGNAL_EXECUTION_REPLAY only and cannot enter the formal full-pipeline path"
    )


def cmd_freeze_stock_rs_pullback_recalculation_v2(args: argparse.Namespace) -> int:
    try:
        return freeze_v2_from_args(args)
    except (FileExistsError, PermissionError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def cmd_validate_stock_rs_pullback_recalculation_manifest_v2(args: argparse.Namespace) -> int:
    try:
        return validate_v2_from_args(args)
    except (FileExistsError, PermissionError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def cmd_run_stock_rs_pullback_recalculation_v2(args: argparse.Namespace) -> int:
    try:
        return run_v2_from_args(args)
    except (FileExistsError, PermissionError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="texperiment")
    parser.add_argument("--root", default=str(ROOT), help="Project root directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("config-check", help="Validate configs and guard rails")
    p.set_defaults(func=cmd_config_check)

    p = sub.add_parser("status", help="Print research status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("ingest-a-share-daily", help="Normalize raw A-share daily bars into canonical parquet")
    p.add_argument("--input", required=True, help="Raw CSV/parquet file or directory")
    p.add_argument("--output", default="data/processed/a_share_daily.parquet", help="Output canonical parquet path")
    p.add_argument("--provider", default="auto", choices=["auto", "canonical", "akshare", "tushare", "baostock"])
    p.add_argument("--adj-type", default="qfq", choices=["none", "qfq", "hfq"], help="Adjustment type of input prices")
    p.add_argument("--source", default=None, help="Optional source label written to output")
    p.add_argument("--allow-quality-warnings", action="store_true", help="Do not fail on duplicate/null/basic quality warnings")
    p.set_defaults(func=cmd_ingest_a_share_daily)

    p = sub.add_parser("data-check", help="Validate canonical daily bars")
    p.add_argument("--path", default="data/processed/a_share_daily.parquet")
    p.add_argument("--allow-quality-warnings", action="store_true")
    p.set_defaults(func=cmd_data_check)

    p = sub.add_parser("ingest-tdx-paired-a-share-daily", help="Join TDX qfq/raw/hfq exports into remediation daily bars")
    p.add_argument("--qfq-input", default="data/raw/tdx_text/qfq")
    p.add_argument("--raw-input", default="data/raw/tdx_text/raw")
    p.add_argument("--hfq-input", default="data/raw/tdx_text/hfq")
    p.add_argument("--output", default="data/processed/a_share_daily_remediation.parquet")
    p.add_argument("--allow-quality-warnings", action="store_true")
    p.set_defaults(func=cmd_ingest_tdx_paired_a_share_daily)

    p = sub.add_parser(
        "prepare-stock-rs-pullback-core-input-pair",
        help="Build an audited canonical raw/qfq candidate pair from frozen TDX exports",
    )
    p.add_argument("--raw-input", default="data/raw/tdx_text/raw")
    p.add_argument("--qfq-input", default="data/raw/tdx_text/qfq")
    p.add_argument("--hfq-input", default="data/raw/tdx_text/hfq")
    p.add_argument("--output-root", required=True, help="New candidate directory; must not exist")
    p.add_argument(
        "--diagnostics",
        default="diagnostics/STOCK_RS_PULLBACK_v1_CORE_INPUT_PAIR_REMEDIATION_1/pair_validation_failure.json",
    )
    p.set_defaults(func=cmd_prepare_stock_rs_pullback_core_input_pair)

    p = sub.add_parser("ingest-tdx-export-index-daily", help="Read one TDX index text export")
    p.add_argument("--input", required=True)
    p.add_argument("--output", default="data/processed/index_daily.parquet")
    p.add_argument("--code", default="000300.SH")
    p.set_defaults(func=cmd_ingest_tdx_export_index_daily)


    p = sub.add_parser("compute-a-share-indicators", help="Compute MA/returns/relative strength/pullback indicators for A-share stocks")
    p.add_argument("--daily-input", default="data/processed/a_share_daily.parquet", help="Canonical A-share daily bars")
    p.add_argument("--benchmark-input", default=None, help="Canonical index daily bars; omit if benchmark rows are inside daily-input")
    p.add_argument("--output", default="data/processed/a_share_indicators.parquet", help="Output indicators parquet/csv")
    p.add_argument("--setup", default="STOCK_RS_PULLBACK_v1", help="Setup config id")
    p.add_argument("--benchmark-code", default=None, help="Override benchmark code, default from setup config")
    p.add_argument("--batch-size", type=_positive_int, default=250_000, help="Parquet rows per batch")
    p.set_defaults(func=cmd_compute_a_share_indicators)

    p = sub.add_parser("build-a-share-universe", help="Build executable A-share universe for the setup")
    p.add_argument("--input", default="data/processed/a_share_daily.parquet", help="Canonical daily bars parquet")
    p.add_argument("--output", default="data/processed/a_share_universe.parquet", help="Output universe parquet")
    p.add_argument("--setup", default="STOCK_RS_PULLBACK_v1", help="Setup config id")
    p.add_argument("--as-of", default=None, help="Optional trading date, e.g. 2026-07-15")
    p.add_argument("--include-rejected", action="store_true", help="Write rejected rows too, with reject_reasons")
    p.add_argument("--allow-empty", action="store_true", help="Allow 0-row output")
    p.add_argument("--batch-size", type=_positive_int, default=250_000, help="Parquet rows per batch")
    p.set_defaults(func=cmd_build_a_share_universe)

    p = sub.add_parser(
        "generate-stock-rs-pullback-signals",
        help="Generate STOCK_RS_PULLBACK_v1 pullback/reclaim signals",
    )
    p.add_argument("--indicator-input", default="data/processed/a_share_indicators.parquet")
    p.add_argument("--universe-input", default=None)
    p.add_argument("--output", default="data/signals/STOCK_RS_PULLBACK_v1_signals.csv")
    p.add_argument("--setup", default="STOCK_RS_PULLBACK_v1")
    p.add_argument("--include-candidates", action="store_true")
    p.add_argument("--require-universe", action="store_true")
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--batch-size", type=_positive_int, default=250_000, help="Parquet rows per batch")
    p.set_defaults(func=cmd_generate_stock_rs_pullback_signals)

    p = sub.add_parser(
        "backtest-stock-rs-pullback",
        help="Backtest STOCK_RS_PULLBACK_v1 triggered signals",
    )
    p.add_argument("--signal-input", default="data/signals/STOCK_RS_PULLBACK_v1_signals.csv")
    p.add_argument("--daily-input", default="data/processed/a_share_daily.parquet")
    p.add_argument("--output", default="data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv")
    p.add_argument("--setup", default="STOCK_RS_PULLBACK_v1")
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--batch-size", type=_positive_int, default=250_000, help="Parquet rows per batch")
    p.set_defaults(func=cmd_backtest_stock_rs_pullback)

    p = sub.add_parser(
        "report-stock-rs-pullback",
        help="Generate STOCK_RS_PULLBACK_v1 validation metrics and report",
    )
    p.add_argument("--trade-input", default="data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv")
    p.add_argument("--metadata-input", default=None)
    p.add_argument("--metrics-output", default="data/reports/STOCK_RS_PULLBACK_v1_metrics.json")
    p.add_argument("--report-output", default="data/reports/STOCK_RS_PULLBACK_v1_validation_report.md")
    p.add_argument("--yearly-output", default="data/reports/STOCK_RS_PULLBACK_v1_yearly.csv")
    p.add_argument("--industry-output", default="data/reports/STOCK_RS_PULLBACK_v1_industry.csv")
    p.add_argument("--setup", default="STOCK_RS_PULLBACK_v1")
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--batch-size", type=_positive_int, default=250_000, help="Metadata rows per batch")
    p.set_defaults(func=cmd_report_stock_rs_pullback)

    p = sub.add_parser(
        "account-sim-stock-rs-pullback",
        help="Simulate STOCK_RS_PULLBACK_v1 trades in the account",
    )
    p.add_argument("--trade-input", default="data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv")
    p.add_argument("--metrics-input", default="data/reports/STOCK_RS_PULLBACK_v1_metrics.json")
    p.add_argument("--output", default="data/account_sim/STOCK_RS_PULLBACK_v1_account_sim.csv")
    p.add_argument("--summary-output", default="data/account_sim/STOCK_RS_PULLBACK_v1_account_summary.json")
    p.add_argument("--report-output", default="data/reports/STOCK_RS_PULLBACK_v1_account_simulation_report.md")
    p.add_argument("--setup", default="STOCK_RS_PULLBACK_v1")
    p.add_argument("--force-research", action="store_true")
    p.set_defaults(func=cmd_account_sim_stock_rs_pullback)

    p = sub.add_parser(
        "generate-stock-rs-pullback-tickets",
        help="Generate manual-review tickets from accepted account trades",
    )
    p.add_argument("--account-sim-input", default="data/account_sim/STOCK_RS_PULLBACK_v1_account_sim.csv")
    p.add_argument("--summary-input", default="data/account_sim/STOCK_RS_PULLBACK_v1_account_summary.json")
    p.add_argument("--output-dir", default="data/tickets/draft")
    p.add_argument("--index-output", default="data/tickets/STOCK_RS_PULLBACK_v1_ticket_index.csv")
    p.add_argument("--summary-output", default="data/tickets/STOCK_RS_PULLBACK_v1_ticket_summary.json")
    p.add_argument("--report-output", default="data/reports/STOCK_RS_PULLBACK_v1_ticket_generation_report.md")
    p.add_argument("--setup", default="STOCK_RS_PULLBACK_v1")
    p.add_argument("--trade-id", default=None)
    p.add_argument("--simulation-id", default=None)
    p.set_defaults(func=cmd_generate_stock_rs_pullback_tickets)

    p = sub.add_parser(
        "prepare-stock-rs-pullback-audit",
        help="Freeze v1 audit inputs and select deterministic samples",
    )
    p.add_argument("--daily-input", default="data/processed/a_share_daily.parquet")
    p.add_argument("--indicator-input", default="data/processed/a_share_indicators.parquet")
    p.add_argument("--universe-input", default="data/processed/a_share_universe_full.parquet")
    p.add_argument("--signal-input", default="data/signals/STOCK_RS_PULLBACK_v1_signals.csv")
    p.add_argument("--trade-input", default="data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv")
    p.add_argument("--output-dir", default="diagnostics/STOCK_RS_PULLBACK_v1")
    p.add_argument("--batch-size", type=_positive_int, default=250_000)
    p.set_defaults(func=cmd_prepare_stock_rs_pullback_audit)

    p = sub.add_parser(
        "freeze-stock-rs-pullback-recalculation",
        help="Freeze full recalculation inputs and engine provenance",
    )
    p.add_argument(
        "--output",
        default="diagnostics/STOCK_RS_PULLBACK_v1/STOCK_RS_PULLBACK_v1_RECALCULATED_manifest.json",
    )
    p.set_defaults(func=cmd_freeze_stock_rs_pullback_recalculation)

    p = sub.add_parser(
        "run-stock-rs-pullback-recalculation",
        help="Run immutable full recalculation from a committed manifest",
    )
    p.add_argument(
        "--manifest",
        default="diagnostics/STOCK_RS_PULLBACK_v1/STOCK_RS_PULLBACK_v1_RECALCULATED_manifest.json",
    )
    p.set_defaults(func=cmd_run_stock_rs_pullback_recalculation)

    p = sub.add_parser(
        "freeze-stock-rs-pullback-recalculation-v2",
        help="Freeze a formal FULL_PIPELINE_RECALCULATION_V2 Manifest",
    )
    p.add_argument("--run-id", required=True)
    p.add_argument("--raw-daily", default="data/processed/a_share_daily_raw.parquet")
    p.add_argument("--qfq-daily", default="data/processed/a_share_daily.parquet")
    p.add_argument("--benchmark", default="data/processed/index_daily.parquet")
    p.add_argument("--setup-config", default="configs/setups/STOCK_RS_PULLBACK_v1.yaml")
    p.add_argument("--cost-config", default="configs/setups/STOCK_RS_PULLBACK_v1.yaml")
    p.add_argument(
        "--st-overrides",
        default="diagnostics/STOCK_RS_PULLBACK_v1/remediation_v5_passed/STOCK_RS_PULLBACK_v1_remediation_manifest.json",
    )
    p.add_argument(
        "--archive-manifest",
        default="diagnostics/STOCK_RS_PULLBACK_v1/STOCK_RS_PULLBACK_v1_audit_manifest.json",
    )
    p.add_argument(
        "--output",
        default="data/recalculations/manifests/STOCK_RS_PULLBACK_v1_RECALCULATED_manifest_v2.json",
    )
    p.set_defaults(func=cmd_freeze_stock_rs_pullback_recalculation_v2)

    p = sub.add_parser(
        "validate-stock-rs-pullback-recalculation-manifest-v2",
        help="Validate a formal V2 recalculation Manifest without running it",
    )
    p.add_argument("--manifest", required=True)
    p.set_defaults(func=cmd_validate_stock_rs_pullback_recalculation_manifest_v2)

    p = sub.add_parser(
        "run-stock-rs-pullback-recalculation-v2",
        help="Run the audited eight-stage pipeline from a formal V2 Manifest",
    )
    p.add_argument("--manifest", required=True)
    p.set_defaults(func=cmd_run_stock_rs_pullback_recalculation_v2)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _assert_setup_action_allowed(root: Path, setup_id: str, action: str) -> None:
    registry = load_yaml(root / "experiment_registry.yaml")
    setup = _registered_setups(registry).get(setup_id)
    if setup is None:
        raise SystemExit(f"setup not registered: {setup_id}")
    permission_field = {
        "account simulation": "account_simulation_allowed",
        "formal ticket generation": "ticket_generation_allowed",
    }.get(action)
    if permission_field is not None and setup.get(permission_field) is not True:
        raise SystemExit(f"{action} blocked by {permission_field}=false for setup {setup_id}")
    status = str(setup.get("lifecycle_status", setup.get("status", "")))
    if is_archived(status):
        raise SystemExit(f"{action} blocked for archived setup {setup_id}: {status}")


def _assert_full_recalculation_allowed(root: Path) -> None:
    registry = load_yaml(root / "experiment_registry.yaml")
    experiment_status = str(registry.get("Trading_Experiment", {}).get("status", ""))
    task = registry.get("full_pipeline_recalculation_tasks", {}).get(
        "FULL_PIPELINE_RECALCULATION_IMPLEMENTATION_v2",
        {},
    )
    authorized = (
        experiment_status == "recalculation_authorized"
        and task.get("status") == "recalculation_authorized"
        and task.get("implementation_frozen") is True
        and task.get("implementation_audited") is True
        and task.get("implementation_audit_decision") == "IMPLEMENTATION_AUDIT_PASSED"
        and task.get("full_recalculation_allowed") is True
    )
    if not authorized:
        raise SystemExit(
            "full pipeline recalculation blocked until V2 implementation audit passes and a new engine commit is frozen"
        )


def _assert_recalculated_paths(*paths: str) -> None:
    required = "STOCK_RS_PULLBACK_v1_RECALCULATED"
    invalid = [str(path) for path in paths if required not in str(path)]
    if invalid:
        raise SystemExit(f"recalculation must use {required} paths; rejected: {invalid}")


def _registered_setups(registry: dict) -> dict[str, dict]:
    setups = registry.get("setups", {})
    if isinstance(setups, dict):
        return setups
    return {str(item.get("id")): item for item in setups if isinstance(item, dict) and item.get("id")}


def _read_metrics_metadata(path: Path, trades: pd.DataFrame, batch_size: int) -> pd.DataFrame:
    codes = set(trades["code"].astype(str)) if "code" in trades.columns else set()
    columns = ["date", "code", "industry", "name"]
    if path.suffix.lower() != ".parquet":
        metadata = read_table(path)
        return metadata.loc[metadata["code"].astype(str).isin(codes)].copy() if codes else metadata
    parquet = pq.ParquetFile(path)
    frames: list[pd.DataFrame] = []
    available = set(parquet.schema_arrow.names)
    selected = [column for column in columns if column in available]
    for batch in parquet.iter_batches(batch_size=batch_size, columns=selected):
        frame = batch.to_pandas()
        if codes and "code" in frame.columns:
            frame = frame.loc[frame["code"].astype(str).isin(codes)]
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=selected)


def _quality_report_to_json(report) -> str:
    return json.dumps(report.__dict__, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())

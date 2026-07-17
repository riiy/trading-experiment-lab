from __future__ import annotations

import argparse
import json
from pathlib import Path

from texperiment.config.loader import load_yaml
from texperiment.config.validator import validate_global_account_config, validate_setup_config
from texperiment.data.loaders import ingest_a_share_daily, read_daily_bars, write_parquet
from texperiment.data.quality import validate_daily_bars
from texperiment.data.tdx_export_source import write_tdx_index_parquet
from texperiment.indicators.a_share import (
    AShareIndicatorConfig,
    build_a_share_indicators,
    write_a_share_indicators_from_parquet,
    write_indicators,
)
from texperiment.guards.trading_permission import assert_trading_disabled
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
    setup = load_yaml(root / "configs" / "setups" / "STOCK_RS_PULLBACK_v1.yaml")
    registry = load_yaml(root / "experiment_registry.yaml")

    validate_global_account_config(account)
    validate_setup_config(setup)
    assert_trading_disabled(registry)

    print("config-check: OK")
    print("trading_allowed: false")
    print("current_setup: STOCK_RS_PULLBACK_v1")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    registry = load_yaml(root / "experiment_registry.yaml")
    exp = registry.get("Trading_Experiment", {})
    print(f"status: {exp.get('status')}")
    print(f"current_setup: {exp.get('current_setup')}")
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


def _quality_report_to_json(report) -> str:
    return json.dumps(report.__dict__, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())

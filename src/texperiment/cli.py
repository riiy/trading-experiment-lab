from __future__ import annotations

import argparse
import json
from pathlib import Path

from texperiment.config.loader import load_yaml
from texperiment.config.validator import validate_global_account_config, validate_setup_config
from texperiment.data.akshare_source import fetch_a_share_daily
from texperiment.data.loaders import ingest_a_share_daily, read_daily_bars, write_parquet
from texperiment.data.quality import validate_daily_bars
from texperiment.data.tdx_source import write_tdx_parquet
from texperiment.data.tdx_export_source import write_tdx_export_parquet
from texperiment.guards.trading_permission import assert_trading_disabled
from texperiment.universe.a_share import (
    AShareUniverseConfig,
    build_a_share_universe,
    build_a_share_universe_from_parquet,
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


def cmd_fetch_a_share_daily(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    output_path = _resolve(root, args.output)
    df, report = fetch_a_share_daily(
        args.start_date,
        args.end_date,
        adj_type=args.adj_type,
        pause_seconds=args.pause,
        max_retries=args.max_retries,
    )
    quality = validate_daily_bars(df, strict=not args.allow_quality_warnings)
    write_parquet(df, output_path)
    print(f"fetch-a-share-daily: OK -> {output_path}")
    print(f"symbols_requested: {report.symbols_requested}")
    print(f"symbols_succeeded: {report.symbols_succeeded}")
    print(f"symbols_failed: {report.symbols_failed}")
    print(_quality_report_to_json(quality))
    return 0


def cmd_ingest_tdx_a_share_daily(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    output_path = _resolve(root, args.output)
    report = write_tdx_parquet(
        args.input,
        output_path,
        adj_type=args.adj_type,
        strict=not args.allow_quality_warnings,
    )
    print(f"ingest-tdx-a-share-daily: OK -> {output_path}")
    print(_quality_report_to_json(report))
    return 0


def cmd_ingest_tdx_export_a_share_daily(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    output_path = _resolve(root, args.output)
    report, ingest_report = write_tdx_export_parquet(
        args.input,
        output_path,
        strict=not args.allow_quality_warnings,
    )
    print(f"ingest-tdx-export-a-share-daily: OK -> {output_path}")
    print(f"files_seen: {ingest_report.files_seen}")
    print(f"files_ingested: {ingest_report.files_ingested}")
    print(f"files_skipped: {ingest_report.files_skipped}")
    print(f"stock_count: {ingest_report.stock_count}")
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
        universe = build_a_share_universe_from_parquet(
            input_path,
            as_of_date=args.as_of,
            config=universe_config,
            include_rejected=args.include_rejected,
        )
    else:
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

    p = sub.add_parser("fetch-a-share-daily", help="Fetch full-market A-share daily bars from AkShare")
    p.add_argument("--start-date", required=True, help="Start date in YYYYMMDD format")
    p.add_argument("--end-date", required=True, help="End date in YYYYMMDD format")
    p.add_argument("--output", default="data/processed/a_share_daily.parquet")
    p.add_argument("--adj-type", default="qfq", choices=["none", "qfq", "hfq"])
    p.add_argument("--pause", type=float, default=0.2)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--allow-quality-warnings", action="store_true")
    p.set_defaults(func=cmd_fetch_a_share_daily)

    p = sub.add_parser("ingest-tdx-a-share-daily", help="Read TongdaXin vipdoc .day files")
    p.add_argument("--input", required=True, help="TongdaXin vipdoc/T0002 directory")
    p.add_argument("--output", default="data/processed/a_share_daily.parquet")
    p.add_argument("--adj-type", default="none", choices=["none"])
    p.add_argument("--allow-quality-warnings", action="store_true")
    p.set_defaults(func=cmd_ingest_tdx_a_share_daily)

    p = sub.add_parser("ingest-tdx-export-a-share-daily", help="Read TDX GB18030 text exports")
    p.add_argument("--input", required=True, help="Directory containing market#code.txt exports")
    p.add_argument("--output", default="data/processed/a_share_daily.parquet")
    p.add_argument("--allow-quality-warnings", action="store_true")
    p.set_defaults(func=cmd_ingest_tdx_export_a_share_daily)

    p = sub.add_parser("build-a-share-universe", help="Build executable A-share universe for the setup")
    p.add_argument("--input", default="data/processed/a_share_daily.parquet", help="Canonical daily bars parquet")
    p.add_argument("--output", default="data/processed/a_share_universe.parquet", help="Output universe parquet")
    p.add_argument("--setup", default="STOCK_RS_PULLBACK_v1", help="Setup config id")
    p.add_argument("--as-of", default=None, help="Optional trading date, e.g. 2026-07-15")
    p.add_argument("--include-rejected", action="store_true", help="Write rejected rows too, with reject_reasons")
    p.add_argument("--allow-empty", action="store_true", help="Allow 0-row output")
    p.set_defaults(func=cmd_build_a_share_universe)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _quality_report_to_json(report) -> str:
    return json.dumps(report.__dict__, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())

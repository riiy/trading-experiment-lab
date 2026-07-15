from __future__ import annotations

import argparse
from pathlib import Path

from texperiment.config.loader import load_yaml
from texperiment.config.validator import validate_global_account_config, validate_setup_config
from texperiment.guards.trading_permission import assert_trading_disabled

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="texperiment")
    parser.add_argument("--root", default=str(ROOT), help="Project root directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("config-check", help="Validate configs and guard rails")
    p.set_defaults(func=cmd_config_check)

    p = sub.add_parser("status", help="Print research status")
    p.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

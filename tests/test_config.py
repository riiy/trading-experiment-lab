from pathlib import Path

from texperiment.config.loader import load_yaml
from texperiment.config.validator import validate_global_account_config, validate_setup_config

ROOT = Path(__file__).resolve().parents[1]


def test_global_account_config_valid():
    config = load_yaml(ROOT / "configs" / "global_account.yaml")
    validate_global_account_config(config)


def test_setup_config_valid():
    config = load_yaml(ROOT / "configs" / "setups" / "STOCK_RS_PULLBACK_v1.yaml")
    validate_setup_config(config)

import pytest

from texperiment.exceptions import PermissionDenied
from texperiment.guards.trading_permission import assert_can_generate_formal_ticket, assert_trading_disabled
from texperiment.guards.no_live_trade import block_live_trade


def test_trading_disabled_guard_passes():
    assert_trading_disabled({"Trading_Experiment": {"trading_allowed": False}})


def test_trading_enabled_guard_blocks():
    with pytest.raises(PermissionDenied):
        assert_trading_disabled({"Trading_Experiment": {"trading_allowed": True}})


def test_formal_ticket_guard_blocks_before_pass():
    with pytest.raises(PermissionDenied):
        assert_can_generate_formal_ticket("pre_registration", "not_started")


def test_formal_ticket_guard_blocks_archived_setup():
    with pytest.raises(PermissionDenied, match="archived setup"):
        assert_can_generate_formal_ticket("ARCHIVED_NON_TRADABLE", "passed")


def test_no_live_trade_always_blocks():
    with pytest.raises(PermissionDenied):
        block_live_trade()

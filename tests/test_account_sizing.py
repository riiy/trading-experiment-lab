from texperiment.account.position_sizing import size_position


def test_position_sizing_valid():
    result = size_position(entry_price=50, stop_price=47.5)
    assert result.valid
    assert result.shares == 200
    assert result.planned_loss == 500
    assert result.capital_used == 10000


def test_position_sizing_one_lot_too_expensive():
    result = size_position(entry_price=200, stop_price=190)
    assert result.valid is False
    assert result.reason == "invalid_one_lot_too_expensive"

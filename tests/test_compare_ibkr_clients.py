from dataclasses import dataclass
from importlib import util
from pathlib import Path
import sys


def _load_compare_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "compare_ibkr_clients.py"
    spec = util.spec_from_file_location("compare_ibkr_clients", script_path)
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    quantity: float


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    last_price: float


class FakeSocketClient:
    def get_balance(self):
        return {"NetLiquidation": [{"amount": 1000.0, "currency": "USD"}]}

    def get_positions(self):
        return [PositionSnapshot(symbol="AAPL", quantity=10)]

    def get_quote(self, symbol: str):
        assert symbol == "AAPL"
        return QuoteSnapshot(symbol=symbol, last_price=200.5)


class FakeRESTClient:
    def get_balance(self):
        return {"NetLiquidation": [{"amount": 1000.0, "currency": "USD"}]}

    def get_positions(self):
        return [PositionSnapshot(symbol="AAPL", quantity=11)]

    def get_quote(self, symbol: str):
        assert symbol == "AAPL"
        return QuoteSnapshot(symbol=symbol, last_price=200.5)


def test_compare_clients_marks_matching_and_mismatching_sections():
    module = _load_compare_module()

    result = module.compare_clients(FakeSocketClient(), FakeRESTClient(), symbol="AAPL")

    assert set(result.keys()) == {"balance", "positions", "quote"}

    balance = result["balance"]
    assert balance["match"] is True
    assert balance["socket"] == {"NetLiquidation": [{"amount": 1000.0, "currency": "USD"}]}
    assert balance["rest"] == {"NetLiquidation": [{"amount": 1000.0, "currency": "USD"}]}

    positions = result["positions"]
    assert positions["match"] is False
    assert positions["socket"] == [{"symbol": "AAPL", "quantity": 10}]
    assert positions["rest"] == [{"symbol": "AAPL", "quantity": 11}]

    quote = result["quote"]
    assert quote["match"] is True
    assert quote["socket"] == {"symbol": "AAPL", "last_price": 200.5}
    assert quote["rest"] == {"symbol": "AAPL", "last_price": 200.5}

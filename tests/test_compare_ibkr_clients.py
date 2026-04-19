from dataclasses import dataclass
from importlib import util
import json
from pathlib import Path
import sys

import pytest


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


def test_compare_clients_positions_are_order_insensitive():
    module = _load_compare_module()

    class OrderedSocketClient:
        def get_balance(self):
            return {}

        def get_positions(self):
            return [
                PositionSnapshot(symbol="AAPL", quantity=10),
                PositionSnapshot(symbol="MSFT", quantity=5),
            ]

        def get_quote(self, symbol: str):
            return QuoteSnapshot(symbol=symbol, last_price=1.0)

    class ReverseRESTClient:
        def get_balance(self):
            return {}

        def get_positions(self):
            return [
                PositionSnapshot(symbol="MSFT", quantity=5),
                PositionSnapshot(symbol="AAPL", quantity=10),
            ]

        def get_quote(self, symbol: str):
            return QuoteSnapshot(symbol=symbol, last_price=1.0)

    result = module.compare_clients(OrderedSocketClient(), ReverseRESTClient(), symbol="AAPL")

    assert result["positions"]["match"] is True
    assert result["positions"]["socket"] == [
        {"symbol": "AAPL", "quantity": 10},
        {"symbol": "MSFT", "quantity": 5},
    ]
    assert result["positions"]["rest"] == [
        {"symbol": "AAPL", "quantity": 10},
        {"symbol": "MSFT", "quantity": 5},
    ]


def test_main_orchestrates_connect_compare_print_and_disconnect(monkeypatch, capsys):
    module = _load_compare_module()
    state = {
        "socket_connected": False,
        "rest_connected": False,
        "socket_disconnected": False,
        "rest_disconnected": False,
    }

    class FakeSocketClientMain:
        def connect(self):
            state["socket_connected"] = True
            return True

        def disconnect(self):
            state["socket_disconnected"] = True

        def get_balance(self):
            return {"NetLiquidation": [{"amount": 1000.0, "currency": "USD"}]}

        def get_positions(self):
            return [PositionSnapshot(symbol="AAPL", quantity=10)]

        def get_quote(self, symbol: str):
            return QuoteSnapshot(symbol=symbol, last_price=200.5)

    class FakeRESTClientMain:
        def connect(self):
            state["rest_connected"] = True
            return True

        def disconnect(self):
            state["rest_disconnected"] = True

        def get_balance(self):
            return {"NetLiquidation": [{"amount": 1000.0, "currency": "USD"}]}

        def get_positions(self):
            return [PositionSnapshot(symbol="AAPL", quantity=10)]

        def get_quote(self, symbol: str):
            return QuoteSnapshot(symbol=symbol, last_price=200.5)

    monkeypatch.setattr(
        module,
        "_load_client_classes",
        lambda: (FakeSocketClientMain, FakeRESTClientMain),
    )
    monkeypatch.setattr(sys, "argv", ["compare_ibkr_clients.py", "--symbol", "AAPL"])

    exit_code = module.main()

    assert exit_code == 0
    assert state == {
        "socket_connected": True,
        "rest_connected": True,
        "socket_disconnected": True,
        "rest_disconnected": True,
    }
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["balance"]["match"] is True
    assert parsed["positions"]["match"] is True
    assert parsed["quote"]["match"] is True


def test_main_disconnects_both_clients_in_finally_on_connect_failure(monkeypatch):
    module = _load_compare_module()
    state = {
        "socket_disconnected": False,
        "rest_disconnected": False,
    }

    class FakeSocketClientMainFail:
        def connect(self):
            return False

        def disconnect(self):
            state["socket_disconnected"] = True

    class FakeRESTClientMainFail:
        def connect(self):
            return True

        def disconnect(self):
            state["rest_disconnected"] = True

    monkeypatch.setattr(
        module,
        "_load_client_classes",
        lambda: (FakeSocketClientMainFail, FakeRESTClientMainFail),
    )
    monkeypatch.setattr(sys, "argv", ["compare_ibkr_clients.py", "--symbol", "AAPL"])

    with pytest.raises(RuntimeError, match="socket client connect\\(\\) returned False"):
        module.main()

    assert state == {
        "socket_disconnected": True,
        "rest_disconnected": True,
    }

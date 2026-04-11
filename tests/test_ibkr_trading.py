from importlib import util
from pathlib import Path
from types import SimpleNamespace


def _load_ibkr_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ibkr_trading.py"
    spec = util.spec_from_file_location("ibkr_trading", script_path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ibkr_module = _load_ibkr_module()


class FakeEvent(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self

    def clear(self):
        super().clear()


class FakeIB:
    def __init__(self, connect_outcomes=None):
        self.disconnectedEvent = FakeEvent()
        self.connect_calls = []
        self.market_data_types = []
        self.accountSummary = lambda: []
        self._connect_outcomes = list(connect_outcomes or [])
        self.qualify_contract_inputs = []

    def connect(self, host, port, clientId, readonly=False):
        call_details = {
            "host": host,
            "port": port,
            "clientId": clientId,
            "readonly": readonly,
        }
        self.connect_calls.append(call_details)
        if self._connect_outcomes:
            outcome = self._connect_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome

    def reqMarketDataType(self, market_data_type):
        self.market_data_types.append(market_data_type)

    def qualifyContracts(self, contract):
        self.qualify_contract_inputs.append(contract)
        return [contract]


def build_client(monkeypatch, connect_outcomes=None):
    fake_ib = FakeIB(connect_outcomes=connect_outcomes)
    monkeypatch.setattr(ibkr_module.time, "sleep", lambda _: None)
    client = ibkr_module.IBKRTradingClient(
        host="127.0.0.1",
        port=4002,
        client_id=9,
        ib_factory=lambda: fake_ib,
    )
    return client, fake_ib


def test_connect_uses_trading_mode_and_sets_delayed_market_data(monkeypatch):
    client, fake_ib = build_client(monkeypatch)

    assert client.connect() is True
    assert fake_ib.connect_calls == [
        {
            "host": "127.0.0.1",
            "port": 4002,
            "clientId": 9,
            "readonly": False,
        }
    ]
    assert fake_ib.market_data_types == [ibkr_module.MARKET_DATA_TYPE_DELAYED]


def test_disconnect_handler_reconnects_in_trading_mode(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)
    reconnect_handler = fake_ib.disconnectedEvent[0]

    reconnect_handler()

    assert fake_ib.connect_calls == [
        {
            "host": "127.0.0.1",
            "port": 4002,
            "clientId": 9,
            "readonly": False,
        }
    ]
    assert fake_ib.market_data_types == [ibkr_module.MARKET_DATA_TYPE_DELAYED]
    captured = capsys.readouterr()
    assert "重连成功" in captured.out


def _make_summary_item(tag: str, value: str, currency: str, account: str):
    return SimpleNamespace(tag=tag, value=value, currency=currency, account=account)


def test_get_balance_keeps_duplicate_tags_by_currency(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    fake_ib.accountSummary = lambda: [
        _make_summary_item("NetLiquidation", "100", "USD", "ACC-1"),
        _make_summary_item("NetLiquidation", "200.5", "USD", "ACC-2"),
        _make_summary_item("TotalCashValue", "300", "USD", "ACC-1"),
    ]

    balance = client.get_balance()

    assert balance["NetLiquidation"] == [
        {"amount": 100.0, "currency": "USD", "account": "ACC-1"},
        {"amount": 200.5, "currency": "USD", "account": "ACC-2"},
    ]


def _raise_runtime_error(message: str):
    def _inner(*args, **kwargs):
        raise RuntimeError(message)
    return _inner


def test_search_symbol_logs_contract_lookup_failure(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)

    fake_ib.qualifyContracts = _raise_runtime_error("qualification failed")
    capsys.readouterr()

    assert client.search_symbol("AAPL") is None

    captured = capsys.readouterr()
    assert "search_symbol(AAPL)" in captured.err
    assert "qualification failed" in captured.err

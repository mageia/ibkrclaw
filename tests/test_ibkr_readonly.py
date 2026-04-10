from importlib import util
from pathlib import Path
from types import SimpleNamespace


def _load_ibkr_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ibkr_readonly.py"
    spec = util.spec_from_file_location("ibkr_readonly", script_path)
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
    def __init__(self):
        self.disconnectedEvent = FakeEvent()
        self.connect_calls = []
        self.market_data_types = []
        self.accountSummary = lambda: []

    def connect(self, host, port, clientId, readonly=False):
        self.connect_calls.append(
            {
                "host": host,
                "port": port,
                "clientId": clientId,
                "readonly": readonly,
            }
        )

    def reqMarketDataType(self, market_data_type):
        self.market_data_types.append(market_data_type)


def build_client(monkeypatch):
    fake_ib = FakeIB()
    monkeypatch.setattr(ibkr_module, "IB", lambda: fake_ib)
    monkeypatch.setattr(ibkr_module.time, "sleep", lambda _: None)
    client = ibkr_module.IBKRReadOnlyClient(host="127.0.0.1", port=4001, client_id=7)
    return client, fake_ib



def test_connect_uses_readonly_and_sets_delayed_market_data(monkeypatch):
    client, fake_ib = build_client(monkeypatch)

    assert client.connect() is True
    assert fake_ib.connect_calls == [
        {
            "host": "127.0.0.1",
            "port": 4001,
            "clientId": 7,
            "readonly": True,
        }
    ]
    assert fake_ib.market_data_types == [ibkr_module.MARKET_DATA_TYPE_DELAYED]


def test_disconnect_handler_reconnects_in_readonly_mode(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)
    reconnect_handler = fake_ib.disconnectedEvent[0]

    reconnect_handler()

    assert fake_ib.connect_calls == [
        {
            "host": "127.0.0.1",
            "port": 4001,
            "clientId": 7,
            "readonly": True,
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


def test_get_primary_balance_amount_prefers_first_numeric_value():
    balance = {
        "TotalCashValue": [
            {"amount": "", "currency": "USD", "account": "ACC-1"},
            {"amount": "n/a", "currency": "USD", "account": "ACC-2"},
            {"amount": "150.25", "currency": "USD", "account": "ACC-3"},
        ]
    }

    assert ibkr_module.get_primary_balance_amount(balance, "TotalCashValue") == 150.25
    assert ibkr_module.get_primary_balance_amount(balance, "NonExistent") == 0.0

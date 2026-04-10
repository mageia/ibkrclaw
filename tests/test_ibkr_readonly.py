from importlib import util
from pathlib import Path
from types import SimpleNamespace
import sys


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
    def __init__(self, connect_outcomes=None):
        self.disconnectedEvent = FakeEvent()
        self.connect_calls = []
        self.market_data_types = []
        self.accountSummary = lambda: []
        self._connect_outcomes = list(connect_outcomes or [])

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


def build_client(monkeypatch, connect_outcomes=None):
    fake_ib = FakeIB(connect_outcomes=connect_outcomes)
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


def test_retries_with_backoff_until_success(monkeypatch, capsys):
    sleep_calls = []
    client, fake_ib = build_client(
        monkeypatch,
        connect_outcomes=[
            RuntimeError("first failure"),
            RuntimeError("second failure"),
        ],
    )
    monkeypatch.setattr(ibkr_module.time, "sleep", sleep_calls.append)
    reconnect_handler = fake_ib.disconnectedEvent[0]
    capsys.readouterr()

    reconnect_handler()

    assert len(fake_ib.connect_calls) == 3
    assert all(call["readonly"] for call in fake_ib.connect_calls)
    base_delay = getattr(ibkr_module, "RECONNECT_BASE_DELAY_SECONDS", None)
    assert base_delay is not None, "RECONNECT_BASE_DELAY_SECONDS is required for reconnect backoff"
    assert sleep_calls == [
        base_delay * i for i in range(1, 4)
    ]
    assert ibkr_module.MARKET_DATA_TYPE_DELAYED in fake_ib.market_data_types
    captured = capsys.readouterr()
    assert "第 1 次重连失败" in captured.out
    assert "第 2 次重连失败" in captured.out
    assert "重连成功" in captured.out


def test_stops_after_max_attempts(monkeypatch, capsys):
    max_attempts = getattr(ibkr_module, "RECONNECT_MAX_ATTEMPTS", None)
    assert max_attempts is not None, "RECONNECT_MAX_ATTEMPTS is required for reconnect backoff"
    base_delay = getattr(ibkr_module, "RECONNECT_BASE_DELAY_SECONDS", None)
    assert base_delay is not None, "RECONNECT_BASE_DELAY_SECONDS is required for reconnect backoff"

    client, fake_ib = build_client(
        monkeypatch,
        connect_outcomes=[RuntimeError("still-down")] * max_attempts,
    )
    reconnect_handler = fake_ib.disconnectedEvent[0]
    capsys.readouterr()

    sleep_calls = []
    monkeypatch.setattr(ibkr_module.time, "sleep", sleep_calls.append)

    reconnect_handler()

    assert len(fake_ib.connect_calls) == max_attempts
    assert sleep_calls == [
        base_delay * i for i in range(1, max_attempts + 1)
    ]
    captured = capsys.readouterr()
    assert "已达到最大重试次数" in captured.out
    assert "still-down" in captured.out


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


def _make_contract(symbol: str, conid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(conId=conid, symbol=symbol, localSymbol=symbol, description=symbol)


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


def test_get_fundamentals_logs_snapshot_failure_and_returns_partial_data(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)
    fake_contract = _make_contract("AAPL", conid=101)
    fake_ib.qualifyContracts = lambda contract: [fake_contract]
    fake_ib.reqFundamentalData = _raise_runtime_error("snapshot failed")
    fake_ib.reqTickers = lambda contract: [SimpleNamespace(high=150, low=140)]
    capsys.readouterr()

    data = client.get_fundamentals("AAPL")

    assert data is not None
    assert data.high_52w == "150"
    assert data.low_52w == "140"

    captured = capsys.readouterr()
    assert "get_fundamentals(AAPL)" in captured.err
    assert "snapshot failed" in captured.err


def test_get_company_news_logs_request_failure(monkeypatch, capsys):
    client, _ = build_client(monkeypatch)

    fake_requests = SimpleNamespace(get=_raise_runtime_error("network failure"))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    capsys.readouterr()

    assert client.get_company_news("LMND") == []

    captured = capsys.readouterr()
    assert "get_company_news(LMND)" in captured.err
    assert "network failure" in captured.err

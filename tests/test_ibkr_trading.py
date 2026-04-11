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
        self.portfolio = lambda: []
        self._req_tickers = lambda contract: []
        self._req_fundamental = lambda contract, report: ""
        self._req_historical = lambda *args, **kwargs: []
        self._req_scanner = lambda *args, **kwargs: []
        self._connect_outcomes = list(connect_outcomes or [])
        self.qualify_contract_inputs = []
        self.req_tickers_calls = []
        self.req_historical_calls = []
        self.req_scanner_calls = []

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

    def reqTickers(self, contract):
        self.req_tickers_calls.append(contract)
        return self._req_tickers(contract)

    def reqFundamentalData(self, contract, report):
        return self._req_fundamental(contract, report)

    def reqHistoricalData(self, *args, **kwargs):
        self.req_historical_calls.append({"args": args, "kwargs": kwargs})
        return self._req_historical(*args, **kwargs)

    def reqScannerData(self, *args, **kwargs):
        self.req_scanner_calls.append({"args": args, "kwargs": kwargs})
        return self._req_scanner(*args, **kwargs)


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


def _make_contract(symbol: str, conid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        conId=conid,
        symbol=symbol,
        localSymbol=symbol,
        description=symbol,
    )


def test_search_symbol_logs_contract_lookup_failure(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)

    fake_ib.qualifyContracts = _raise_runtime_error("qualification failed")
    capsys.readouterr()

    assert client.search_symbol("AAPL") is None

    captured = capsys.readouterr()
    assert "search_symbol(AAPL)" in captured.err
    assert "qualification failed" in captured.err


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


def test_get_positions_returns_server_side_pnl(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=101)
    fake_ib.portfolio = lambda: [
        SimpleNamespace(
            contract=contract,
            position=10,
            averageCost=20.0,
            marketValue=250.0,
            unrealizedPNL=50.0,
        )
    ]

    positions = client.get_positions()

    assert len(positions) == 1
    position = positions[0]
    assert position.symbol == "AAPL"
    assert position.conid == 101
    assert position.quantity == 10
    assert position.avg_cost == 20.0
    assert position.market_value == 250.0
    assert position.unrealized_pnl == 50.0
    assert position.pnl_percent == 25.0


def test_get_quote_uses_snapshot_data(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=202)
    fake_ib.qualifyContracts = lambda _: [contract]

    ticker = SimpleNamespace(
        last=101.5,
        close=100.0,
        bid=101.0,
        ask=102.0,
        volume=1500,
    )
    fake_ib._req_tickers = lambda contract: [ticker]

    quote = client.get_quote("AAPL")

    assert quote is not None
    assert quote.conid == 202
    assert quote.symbol == "AAPL"
    assert quote.last_price == 101.5
    assert quote.bid == 101.0
    assert quote.ask == 102.0
    assert quote.volume == 1500
    assert quote.change == 1.5
    assert quote.change_pct == 1.5
    assert fake_ib.req_tickers_calls == [contract]


def test_get_fundamentals_logs_snapshot_failure_and_returns_partial_data(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)
    fake_contract = _make_contract("AAPL", conid=303)
    fake_ib.qualifyContracts = lambda _: [fake_contract]
    fake_ib._req_fundamental = _raise_runtime_error("snapshot failed")
    fake_ib._req_tickers = lambda contract: [SimpleNamespace(high=150, low=140)]
    capsys.readouterr()

    data = client.get_fundamentals("AAPL")

    assert data is not None
    assert data.high_52w == "150"
    assert data.low_52w == "140"

    captured = capsys.readouterr()
    assert "get_fundamentals(AAPL)" in captured.err
    assert "snapshot failed" in captured.err


def test_get_historical_data_returns_serialized_bars(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=404)
    fake_ib.qualifyContracts = lambda _: [contract]
    fake_ib._req_historical = lambda *args, **kwargs: [
        SimpleNamespace(
            date="2024-01-02",
            open=10.0,
            high=12.0,
            low=9.0,
            close=11.0,
            volume=1000,
        )
    ]

    bars = client.get_historical_data("AAPL", duration="1 W", bar_size="1 day")

    assert bars == [
        {
            "date": "2024-01-02",
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "volume": 1000,
        }
    ]
    assert fake_ib.req_historical_calls


def test_run_scanner_returns_ranked_results(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    fake_ib._req_scanner = lambda *args, **kwargs: [
        SimpleNamespace(
            rank=1,
            contractDetails=SimpleNamespace(
                contract=SimpleNamespace(symbol="AAPL", conId=5001)
            ),
            distance="2.5",
            benchmark="0.0",
            projection="1.2",
        )
    ]

    results = client.run_scanner(scan_type="TOP_PERC_GAIN", size=5)

    assert results == [
        {
            "rank": 1,
            "symbol": "AAPL",
            "conid": 5001,
            "distance": "2.5",
            "benchmark": "0.0",
            "projection": "1.2",
        }
    ]
    assert fake_ib.req_scanner_calls


def test_get_company_news_logs_request_failure(monkeypatch, capsys):
    client, _ = build_client(monkeypatch)
    fake_requests = SimpleNamespace(get=_raise_runtime_error("network failure"))
    monkeypatch.setitem(ibkr_module.sys.modules, "requests", fake_requests)
    capsys.readouterr()

    assert client.get_company_news("LMND") == []

    captured = capsys.readouterr()
    assert "get_company_news(LMND)" in captured.err
    assert "network failure" in captured.err

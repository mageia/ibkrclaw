from importlib import util
from pathlib import Path
from types import SimpleNamespace

import pytest


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
        self._place_order = lambda contract, order: SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(
                status="Submitted",
                filled=0,
                remaining=getattr(order, "totalQuantity", 0),
                avgFillPrice=0,
                lastFillPrice=0,
            ),
            fills=[],
        )
        self._connect_outcomes = list(connect_outcomes or [])
        self.qualify_contract_inputs = []
        self.req_tickers_calls = []
        self.req_historical_calls = []
        self.req_scanner_calls = []
        self.place_order_calls = []

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

    def placeOrder(self, contract, order):
        self.place_order_calls.append({"contract": contract, "order": order})
        return self._place_order(contract, order)


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


def _make_contract(
    symbol: str,
    conid: int = 1,
    *,
    sec_type: str = "STK",
    exchange: str = "SMART",
) -> SimpleNamespace:
    return SimpleNamespace(
        conId=conid,
        symbol=symbol,
        localSymbol=symbol,
        description=symbol,
        secType=sec_type,
        exchange=exchange,
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


def test_get_quote_logs_unexpected_ticker_count(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=211)
    fake_ib.qualifyContracts = lambda _: [contract]
    fake_ib._req_tickers = lambda contract: []
    capsys.readouterr()

    assert client.get_quote("AAPL") is None

    captured = capsys.readouterr()
    assert "get_quote(AAPL)" in captured.err
    assert "unexpected ticker count" in captured.err


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


def test_get_company_news_rejects_oversized_response(monkeypatch, capsys):
    client, _ = build_client(monkeypatch)
    max_bytes = ibkr_module.NEWS_MAX_RESPONSE_BYTES
    oversized_text = "x" * (max_bytes + 1)
    fake_response = SimpleNamespace(
        status_code=200,
        text=oversized_text,
        content=oversized_text.encode("utf-8"),
    )
    fake_requests = SimpleNamespace(get=lambda *args, **kwargs: fake_response)
    monkeypatch.setitem(ibkr_module.sys.modules, "requests", fake_requests)
    capsys.readouterr()

    assert client.get_company_news("LMND") == []

    captured = capsys.readouterr()
    assert "get_company_news(LMND)" in captured.err
    assert "response too large" in captured.err


def test_build_contract_supports_stock_option_future():
    stock_spec = ibkr_module.ContractSpec(sec_type="STK", symbol="AAPL")
    option_spec = ibkr_module.ContractSpec(
        sec_type="OPT",
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        last_trade_date_or_contract_month="20250117",
        strike=150.0,
        right="C",
        multiplier="100",
    )
    future_spec = ibkr_module.ContractSpec(
        sec_type="FUT",
        symbol="ES",
        exchange="GLOBEX",
        currency="USD",
        last_trade_date_or_contract_month="202506",
    )

    stock = ibkr_module.build_contract(stock_spec)
    option = ibkr_module.build_contract(option_spec)
    future = ibkr_module.build_contract(future_spec)

    assert stock.secType == "STK"
    assert stock.symbol == "AAPL"
    assert option.secType == "OPT"
    assert option.symbol == "AAPL"
    assert option.lastTradeDateOrContractMonth == "20250117"
    assert option.strike == 150.0
    assert option.right == "C"
    assert future.secType == "FUT"
    assert future.symbol == "ES"
    assert future.lastTradeDateOrContractMonth == "202506"


def test_build_contract_raises_for_incomplete_option():
    incomplete_spec = ibkr_module.ContractSpec(
        sec_type="OPT",
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
    )

    with pytest.raises(ValueError):
        ibkr_module.build_contract(incomplete_spec)


def test_build_contract_requires_future_contract_month():
    spec = ibkr_module.ContractSpec(
        sec_type="FUT",
        symbol="ES",
        exchange="GLOBEX",
        currency="USD",
        local_symbol="ESU5",
    )

    with pytest.raises(ValueError):
        ibkr_module.build_contract(spec)


def test_build_order_supports_market_limit_stop_and_stop_limit():
    contract_spec = ibkr_module.ContractSpec(sec_type="STK", symbol="AAPL")

    market_request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="BUY",
        quantity=10,
        order_type="MKT",
    )
    limit_request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="SELL",
        quantity=5,
        order_type="LMT",
        limit_price=123.45,
    )
    stop_request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="SELL",
        quantity=5,
        order_type="STP",
        stop_price=120.0,
    )
    stop_limit_request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="BUY",
        quantity=5,
        order_type="STP_LMT",
        limit_price=121.0,
        stop_price=120.0,
    )

    market_order = ibkr_module.build_order(market_request)
    limit_order = ibkr_module.build_order(limit_request)
    stop_order = ibkr_module.build_order(stop_request)
    stop_limit_order = ibkr_module.build_order(stop_limit_request)

    assert market_order.orderType == "MKT"
    assert market_order.action == "BUY"
    assert market_order.totalQuantity == 10
    assert limit_order.orderType == "LMT"
    assert limit_order.lmtPrice == 123.45
    assert stop_order.orderType == "STP"
    assert stop_order.auxPrice == 120.0
    assert stop_limit_order.orderType == "STP LMT"
    assert stop_limit_order.lmtPrice == 121.0
    assert stop_limit_order.auxPrice == 120.0


def test_build_order_rejects_unknown_type():
    contract_spec = ibkr_module.ContractSpec(sec_type="STK", symbol="AAPL")
    request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="BUY",
        quantity=1,
        order_type="XYZ",
    )

    with pytest.raises(ValueError):
        ibkr_module.build_order(request)


def test_build_order_rejects_invalid_action():
    contract_spec = ibkr_module.ContractSpec(sec_type="STK", symbol="AAPL")
    request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="HOLD",
        quantity=1,
        order_type="MKT",
    )

    with pytest.raises(ValueError):
        ibkr_module.build_order(request)


@pytest.mark.parametrize("quantity", [0, -1])
def test_build_order_rejects_non_positive_quantity(quantity):
    contract_spec = ibkr_module.ContractSpec(sec_type="STK", symbol="AAPL")
    request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="BUY",
        quantity=quantity,
        order_type="MKT",
    )

    with pytest.raises(ValueError):
        ibkr_module.build_order(request)


@pytest.mark.parametrize(
    ("order_type", "limit_price", "stop_price"),
    [
        ("LMT", None, None),
        ("STP", None, None),
        ("STP_LMT", 120.0, None),
    ],
)
def test_build_order_requires_prices(order_type, limit_price, stop_price):
    contract_spec = ibkr_module.ContractSpec(sec_type="STK", symbol="AAPL")
    request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="BUY",
        quantity=1,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
    )

    with pytest.raises(ValueError):
        ibkr_module.build_order(request)


def _make_trade(contract, order, *, status="Submitted", filled=0, remaining=0):
    order_status = SimpleNamespace(
        status=status,
        filled=filled,
        remaining=remaining,
        avgFillPrice=0,
        lastFillPrice=0,
    )
    return SimpleNamespace(
        contract=contract,
        order=order,
        orderStatus=order_status,
        fills=[],
    )


def test_place_order_raw_qualifies_contract_then_places(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract_spec = ibkr_module.ContractSpec(sec_type="STK", symbol="AAPL")
    order_request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="BUY",
        quantity=2,
        order_type="MKT",
    )
    contract = ibkr_module.build_contract(contract_spec)
    order = ibkr_module.build_order(order_request)

    qualified_contract = _make_contract("AAPL", conid=9001)

    def _qualify(contract):
        fake_ib.qualify_contract_inputs.append(contract)
        return [qualified_contract]

    fake_ib.qualifyContracts = _qualify
    trade = _make_trade(qualified_contract, order, remaining=2)
    fake_ib._place_order = lambda _, __: trade

    result = client.place_order_raw(contract, order)

    assert result is trade
    assert fake_ib.qualify_contract_inputs == [contract]
    assert fake_ib.place_order_calls == [
        {"contract": qualified_contract, "order": order}
    ]


def test_place_order_returns_trade_snapshot(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract_spec = ibkr_module.ContractSpec(sec_type="STK", symbol="AAPL")
    request = ibkr_module.OrderRequest(
        contract=contract_spec,
        action="BUY",
        quantity=3,
        order_type="LMT",
        limit_price=101.5,
        tif="DAY",
    )
    qualified_contract = _make_contract("AAPL", conid=8123)
    fake_ib.qualifyContracts = lambda _: [qualified_contract]

    order = ibkr_module.build_order(request)
    order.orderId = 77
    order.permId = 12345
    order.account = "DU123"
    trade = _make_trade(qualified_contract, order, filled=1, remaining=2)
    fake_ib._place_order = lambda _, __: trade

    snapshot = client.place_order(request)

    assert snapshot.order.order_id == 77
    assert snapshot.order.perm_id == 12345
    assert snapshot.order.symbol == "AAPL"
    assert snapshot.order.sec_type == "STK"
    assert snapshot.order.action == "BUY"
    assert snapshot.order.order_type == "LMT"
    assert snapshot.order.total_quantity == 3
    assert snapshot.order.limit_price == 101.5
    assert snapshot.order.status == "Submitted"
    assert snapshot.order.filled == 1
    assert snapshot.order.remaining == 2
    assert snapshot.order.exchange == "SMART"
    assert snapshot.order.account == "DU123"


def test_trade_snapshot_keeps_missing_fields_as_none():
    trade = SimpleNamespace(
        contract=SimpleNamespace(),
        order=SimpleNamespace(),
        orderStatus=SimpleNamespace(),
        fills=None,
    )

    snapshot = ibkr_module._trade_snapshot_from_trade(trade)

    assert snapshot.order.symbol is None
    assert snapshot.order.sec_type is None
    assert snapshot.order.action is None
    assert snapshot.order.order_type is None
    assert snapshot.order.total_quantity is None
    assert snapshot.order.limit_price is None
    assert snapshot.order.stop_price is None
    assert snapshot.order.status is None
    assert snapshot.order.filled is None
    assert snapshot.order.remaining is None
    assert snapshot.order.avg_fill_price is None
    assert snapshot.order.last_fill_price is None
    assert snapshot.order.exchange is None
    assert snapshot.order.account is None


def test_trade_snapshot_maps_fill_fields():
    fill = SimpleNamespace(
        execution=SimpleNamespace(
            execId="EXEC-1",
            time="2024-01-03 09:30:00",
            price=10.5,
            shares=2,
            exchange="NYSE",
        )
    )
    trade = SimpleNamespace(
        contract=_make_contract("AAPL", conid=9001),
        order=SimpleNamespace(),
        orderStatus=SimpleNamespace(),
        fills=[fill],
    )

    snapshot = ibkr_module._trade_snapshot_from_trade(trade)

    assert len(snapshot.fills) == 1
    recorded = snapshot.fills[0]
    assert recorded.execution_id == "EXEC-1"
    assert recorded.time == "2024-01-03 09:30:00"
    assert recorded.price == 10.5
    assert recorded.quantity == 2
    assert recorded.exchange == "NYSE"


def test_cancel_order_finds_trade_and_cancels(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=9101)
    order = SimpleNamespace(
        orderId=501,
        action="BUY",
        orderType="LMT",
        totalQuantity=2,
        lmtPrice=10.5,
    )
    trade = _make_trade(contract, order, status="Submitted", filled=0, remaining=2)

    fake_ib.trades = lambda: [trade]
    fake_ib.cancel_order_calls = []

    def _cancel(order_to_cancel):
        fake_ib.cancel_order_calls.append(order_to_cancel)

    fake_ib.cancelOrder = _cancel

    snapshot = client.cancel_order(501)

    assert fake_ib.cancel_order_calls == [order]
    assert snapshot.order.order_id == 501
    assert snapshot.order.symbol == "AAPL"
    assert snapshot.order.status == "Submitted"


def test_cancel_order_raises_when_not_found(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    fake_ib.trades = lambda: []
    fake_ib.openOrders = lambda: []
    fake_ib.orders = lambda: []

    with pytest.raises(ValueError) as exc:
        client.cancel_order(999)

    assert "999" in str(exc.value)


def test_cancel_order_raw_accepts_trade_or_order(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=9201)
    trade_order = SimpleNamespace(orderId=701)
    trade = _make_trade(contract, trade_order, status="Submitted", filled=0, remaining=1)
    solo_order = SimpleNamespace(orderId=702)

    fake_ib.cancel_order_calls = []

    def _cancel(order_to_cancel):
        fake_ib.cancel_order_calls.append(order_to_cancel)

    fake_ib.cancelOrder = _cancel

    assert client.cancel_order_raw(trade) is trade
    assert client.cancel_order_raw(solo_order) is solo_order
    assert fake_ib.cancel_order_calls == [trade_order, solo_order]


def test_modify_order_overrides_fields_and_resubmits(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=9301)
    original_order = SimpleNamespace(
        orderId=801,
        action="BUY",
        orderType="LMT",
        totalQuantity=1,
        lmtPrice=10.0,
        auxPrice=9.0,
        tif="DAY",
        outsideRth=False,
        transmit=True,
    )
    trade = _make_trade(contract, original_order, status="Submitted", filled=0, remaining=1)
    fake_ib.trades = lambda: [trade]

    def _place_order(contract_arg, order_arg):
        return _make_trade(contract_arg, order_arg, status="Submitted", filled=0, remaining=2)

    fake_ib._place_order = _place_order

    request = ibkr_module.ModifyOrderRequest(
        order_id=801,
        quantity=2,
        limit_price=10.5,
        stop_price=9.5,
        tif="GTC",
        outside_rth=True,
        transmit=False,
    )

    snapshot = client.modify_order(request)

    assert len(fake_ib.place_order_calls) == 1
    placed_order = fake_ib.place_order_calls[0]["order"]
    assert placed_order.orderId == 801
    assert placed_order.totalQuantity == 2
    assert placed_order.lmtPrice == 10.5
    assert placed_order.auxPrice == 9.5
    assert placed_order.tif == "GTC"
    assert placed_order.outsideRth is True
    assert placed_order.transmit is False
    assert original_order.totalQuantity == 1
    assert original_order.lmtPrice == 10.0
    assert original_order.auxPrice == 9.0
    assert original_order.tif == "DAY"
    assert original_order.outsideRth is False
    assert original_order.transmit is True
    assert snapshot.order.order_id == 801
    assert snapshot.order.total_quantity == 2


def test_orders_and_trades_queries_return_snapshots(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=9401)
    order_status = SimpleNamespace(
        status="Submitted",
        filled=1,
        remaining=4,
        avgFillPrice=10.1,
        lastFillPrice=10.2,
        time="2024-01-03 09:31:00",
    )
    open_order = SimpleNamespace(
        orderId=901,
        permId=7001,
        action="SELL",
        orderType="LMT",
        totalQuantity=5,
        lmtPrice=10.5,
        auxPrice=None,
        account="DU123",
        orderStatus=order_status,
        contract=contract,
    )
    order = SimpleNamespace(
        orderId=902,
        permId=7002,
        action="BUY",
        orderType="MKT",
        totalQuantity=3,
        lmtPrice=None,
        auxPrice=None,
        account="DU124",
        orderStatus=order_status,
        contract=contract,
    )
    trade_order = SimpleNamespace(orderId=903, action="BUY", orderType="LMT", totalQuantity=2)
    trade = _make_trade(contract, trade_order, status="Submitted", filled=0, remaining=2)
    fill = SimpleNamespace(
        execution=SimpleNamespace(
            execId="EXEC-2",
            time="2024-01-03 10:00:00",
            price=10.3,
            shares=1,
            exchange="NASDAQ",
        )
    )

    fake_ib.openOrders = lambda: [open_order]
    fake_ib.orders = lambda: [order]
    fake_ib.trades = lambda: [trade]
    fake_ib.fills = lambda: [fill]

    open_orders = client.get_open_orders()
    orders = client.get_orders()
    trades = client.get_trades()
    fills = client.get_fills()

    assert open_orders[0].order_id == 901
    assert open_orders[0].symbol == "AAPL"
    assert open_orders[0].status == "Submitted"
    assert orders[0].order_id == 902
    assert orders[0].action == "BUY"
    assert trades[0].order.order_id == 903
    assert fills[0].execution_id == "EXEC-2"


def test_get_open_orders_accepts_trade_like(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=9501)
    order = SimpleNamespace(orderId=1001, action="BUY", orderType="LMT", totalQuantity=1)
    trade = _make_trade(contract, order, status="Submitted", filled=0, remaining=1)
    fake_ib.openOrders = lambda: [trade]

    snapshots = client.get_open_orders()

    assert len(snapshots) == 1
    assert snapshots[0].order_id == 1001
    assert snapshots[0].symbol == "AAPL"


def test_get_orders_accepts_trade_like(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=9601)
    order = SimpleNamespace(orderId=1002, action="SELL", orderType="MKT", totalQuantity=2)
    trade = _make_trade(contract, order, status="Submitted", filled=0, remaining=2)
    fake_ib.orders = lambda: [trade]

    snapshots = client.get_orders()

    assert len(snapshots) == 1
    assert snapshots[0].order_id == 1002
    assert snapshots[0].action == "SELL"


def test_cancel_order_fallbacks_to_open_orders(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=9701)
    order = SimpleNamespace(
        orderId=1101,
        action="BUY",
        orderType="LMT",
        totalQuantity=1,
        lmtPrice=9.9,
        contract=contract,
    )
    fake_ib.trades = lambda: []
    fake_ib.openOrders = lambda: [order]
    fake_ib.orders = lambda: []
    fake_ib.cancel_order_calls = []
    fake_ib.cancelOrder = lambda order_to_cancel: fake_ib.cancel_order_calls.append(order_to_cancel)

    snapshot = client.cancel_order(1101)

    assert fake_ib.cancel_order_calls == [order]
    assert snapshot.order.order_id == 1101
    assert snapshot.order.symbol == "AAPL"


def test_modify_order_fallbacks_to_orders(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    contract = _make_contract("AAPL", conid=9801)
    order = SimpleNamespace(
        orderId=1201,
        action="BUY",
        orderType="LMT",
        totalQuantity=1,
        lmtPrice=10.0,
        auxPrice=9.5,
        contract=contract,
    )
    fake_ib.trades = lambda: []
    fake_ib.openOrders = lambda: []
    fake_ib.orders = lambda: [order]

    def _place_order(contract_arg, order_arg):
        return _make_trade(contract_arg, order_arg, status="Submitted", filled=0, remaining=2)

    fake_ib._place_order = _place_order

    request = ibkr_module.ModifyOrderRequest(
        order_id=1201,
        quantity=2,
        limit_price=10.5,
    )

    snapshot = client.modify_order(request)

    assert snapshot.order.order_id == 1201
    assert snapshot.order.total_quantity == 2
    assert len(fake_ib.place_order_calls) == 1

from dataclasses import FrozenInstanceError
from importlib import util
from pathlib import Path
import sys

import pytest


def _load_ibkr_rest_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ibkr_rest_trading.py"
    spec = util.spec_from_file_location("ibkr_rest_trading", script_path)
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


ibkr_rest_module = _load_ibkr_rest_module()


class FakeSession:
    def __init__(self):
        self.closed = False

    def request(self, *args, **kwargs):
        raise AssertionError("request should not be called")

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, status_code, text, payload):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class CapturingSession:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    def close(self):
        self.closed = True


class SequencedSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def test_load_module_does_not_leave_sys_modules_entry():
    assert "ibkr_rest_trading" not in sys.modules


def test_rest_client_uses_injected_session_factory():
    fake_session = FakeSession()
    client = ibkr_rest_module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api",
        default_account_id="DU123",
        session_factory=lambda: fake_session,
    )

    assert client.base_url == "https://localhost:5000/v1/api"
    assert client.default_account_id == "DU123"
    assert client.session is fake_session


def test_request_json_assembles_request_and_returns_json_payload():
    response = FakeResponse(200, "ok", {"result": "success"})
    session = CapturingSession(response)
    client = ibkr_rest_module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api/",
        timeout_seconds=42.5,
        verify_ssl=True,
        session_factory=lambda: session,
    )

    payload = client._request_json(
        "get",
        "/portfolio/accounts",
        params={"currency": "USD"},
        payload={"includeClosed": False},
    )

    assert payload == {"result": "success"}
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/accounts",
            {
                "params": {"currency": "USD"},
                "json": {"includeClosed": False},
                "timeout": 42.5,
                "verify": True,
            },
        )
    ]


def test_request_json_raises_with_method_path_and_status():
    class ErrorSession:
        def request(self, *args, **kwargs):
            return FakeResponse(503, "gateway unavailable", {"ok": False})

        def close(self):
            return None

    client = ibkr_rest_module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api",
        session_factory=lambda: ErrorSession(),
    )

    with pytest.raises(RuntimeError) as exc_info:
        client._request_json("post", "/iserver/account/orders")

    message = str(exc_info.value)
    assert "POST /iserver/account/orders failed" in message
    assert "503" in message
    assert "gateway unavailable" in message


def test_position_is_immutable():
    position = ibkr_rest_module.Position(
        symbol="AAPL",
        conid=1,
        quantity=1.0,
        avg_cost=100.0,
        market_value=101.0,
        unrealized_pnl=1.0,
        pnl_percent=1.0,
    )

    with pytest.raises(FrozenInstanceError):
        position.quantity = 2.0


def test_disconnect_closes_injected_session():
    session = FakeSession()
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    client.disconnect()

    assert session.closed is True


def test_default_timeout_is_float():
    assert isinstance(ibkr_rest_module.DEFAULT_TIMEOUT_SECONDS, float)


def test_connect_authenticates_and_sets_default_account():
    session = SequencedSession(
        [
            FakeResponse(200, "ok", {"authenticated": True}),
            FakeResponse(200, "ok", {"session": "alive"}),
            FakeResponse(200, "ok", [{"id": "DU111"}, {"id": "DU222"}]),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api",
        session_factory=lambda: session,
    )

    assert client.is_authenticated() is False
    assert client.connect() is True
    assert client.is_authenticated() is True
    assert client.default_account_id == "DU111"
    assert client._require_account_id() == "DU111"
    assert client._require_account_id("DU999") == "DU999"

    with pytest.raises(ValueError):
        ibkr_rest_module.IBKRRESTTradingClient()._require_account_id()

    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/auth/status",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "POST",
            "https://localhost:5000/v1/api/tickle",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/accounts",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
    ]


def test_connect_returns_false_when_unauthenticated_and_stops_followups():
    session = SequencedSession([FakeResponse(200, "ok", {"authenticated": False})])
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    assert client.connect() is False
    assert client.is_authenticated() is False
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/auth/status",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        )
    ]


def test_get_accounts_supports_accounts_object_shape():
    session = SequencedSession(
        [FakeResponse(200, "ok", {"accounts": [{"id": "DU111"}, "DU222"]})]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    accounts = client.get_accounts()

    assert accounts == [{"id": "DU111"}, {"id": "DU222"}]


def test_connect_sets_default_account_from_string_account_payload():
    session = SequencedSession(
        [
            FakeResponse(200, "ok", {"authenticated": True}),
            FakeResponse(200, "ok", {"session": "alive"}),
            FakeResponse(200, "ok", ["DU111", "DU222"]),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    assert client.connect() is True
    assert client.default_account_id == "DU111"


def test_get_positions_uses_contract_desc_as_symbol_fallback():
    session = SequencedSession(
        [
            FakeResponse(
                200,
                "ok",
                [
                    {
                        "contractDesc": "SPY",
                        "ticker": "",
                        "conid": 756733,
                        "position": 1,
                        "avgCost": 500,
                        "mktValue": 505,
                        "unrealizedPnl": 5,
                    }
                ],
            ),
            FakeResponse(200, "ok", []),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(
        default_account_id="DU111",
        session_factory=lambda: session,
    )

    positions = client.get_positions()

    assert positions[0].symbol == "SPY"


def test_get_balance_raises_when_no_resolvable_account():
    client = ibkr_rest_module.IBKRRESTTradingClient()

    with pytest.raises(ValueError):
        client.get_balance()


def test_get_balance_keeps_duplicate_tags_and_get_positions_aggregates_pages():
    summary_rows = [
        {"tag": "NetLiquidation", "value": "100000", "currency": "USD", "account": "DU111"},
        {"tag": "NetLiquidation", "value": "90000", "currency": "EUR", "account": "DU111"},
        {"tag": "NetLiquidation", "value": "100000", "currency": "USD", "account": "DU111"},
    ]
    first_page = [
        {
            "ticker": "AAPL",
            "conid": 265598,
            "position": 10,
            "avgCost": 100,
            "mktValue": 1200,
            "unrealizedPnl": 200,
        }
    ]
    second_page = [
        {
            "ticker": "TSLA",
            "conid": 76792991,
            "position": -2,
            "avgCost": 250,
            "mktValue": -460,
            "unrealizedPnl": 40,
        }
    ]
    session = SequencedSession(
        [
            FakeResponse(200, "ok", summary_rows),
            FakeResponse(200, "ok", {"USD": {"cashbalance": 12345}}),
            FakeResponse(200, "ok", first_page),
            FakeResponse(200, "ok", second_page),
            FakeResponse(200, "ok", []),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(
        default_account_id="DU111",
        session_factory=lambda: session,
    )

    balance = client.get_balance()
    positions = client.get_positions()

    assert balance == {
        "NetLiquidation": [
            {"amount": 100000.0, "currency": "USD", "account": "DU111"},
            {"amount": 90000.0, "currency": "EUR", "account": "DU111"},
            {"amount": 100000.0, "currency": "USD", "account": "DU111"},
        ]
    }
    assert [position.symbol for position in positions] == ["AAPL", "TSLA"]
    assert positions[0].pnl_percent == pytest.approx(20.0)
    assert positions[1].pnl_percent == pytest.approx(8.0)
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/DU111/summary",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/DU111/ledger",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/DU111/positions/0",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/DU111/positions/1",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/DU111/positions/2",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
    ]


def test_get_balance_preserves_raw_amount_when_parse_fails():
    session = SequencedSession(
        [
            FakeResponse(
                200,
                "ok",
                [{"tag": "CustomTag", "value": "abc", "currency": "USD", "account": "DU111"}],
            ),
            FakeResponse(200, "ok", {"USD": {"cashbalance": 0}}),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(
        default_account_id="DU111",
        session_factory=lambda: session,
    )

    balance = client.get_balance()

    assert balance == {
        "CustomTag": [{"amount": "abc", "currency": "USD", "account": "DU111"}]
    }
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/DU111/summary",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/DU111/ledger",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
    ]


def test_search_symbol_returns_first_result_and_accepts_kwargs():
    session = SequencedSession(
        [
            FakeResponse(
                200,
                "ok",
                [
                    {
                        "conid": 265598,
                        "symbol": "AAPL",
                        "companyName": "APPLE INC",
                        "description": "NASDAQ",
                    }
                ],
            ),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    contract = client.search_symbol("AAPL", name=True)

    assert contract == {"conid": 265598, "symbol": "AAPL", "companyName": "APPLE INC", "description": "NASDAQ"}
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL", "name": True}, "json": None, "timeout": 10.0, "verify": False},
        )
    ]


def test_search_symbol_returns_none_when_payload_is_empty_list():
    session = SequencedSession([FakeResponse(200, "ok", [])])
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    assert client.search_symbol("AAPL") is None
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL"}, "json": None, "timeout": 10.0, "verify": False},
        )
    ]


def test_get_quote_maps_snapshot_payload_and_uses_symbol_from_search_result():
    session = SequencedSession(
        [
            FakeResponse(200, "ok", [{"conid": 265598, "symbol": "AAPL.US"}]),
            FakeResponse(
                200,
                "ok",
                [{"conid": 265598, "31": "101.5", "84": "101.0", "86": "102.0", "87": "1500", "88": "100.0", "7762": "1.5"}],
            ),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    quote = client.get_quote("AAPL")

    assert quote == ibkr_rest_module.Quote(
        conid=265598,
        symbol="AAPL.US",
        last_price=101.5,
        bid=101.0,
        ask=102.0,
        volume=1500,
        change=1.5,
        change_pct=1.5,
    )
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL"}, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/marketdata/snapshot",
            {
                "params": {"conids": 265598, "fields": "31,84,86,87,88,7762"},
                "json": None,
                "timeout": 10.0,
                "verify": False,
            },
        ),
    ]


def test_get_quote_computes_change_pct_when_7762_missing():
    session = SequencedSession(
        [
            FakeResponse(200, "ok", [{"conid": 265598, "symbol": "AAPL"}]),
            FakeResponse(200, "ok", [{"conid": 265598, "31": "103.0", "88": "100.0", "84": "102.9", "86": "103.1", "87": "10"}]),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    quote = client.get_quote("AAPL")

    assert quote is not None
    assert quote.change == 3.0
    assert quote.change_pct == 3.0


def test_get_quote_returns_none_when_search_result_has_no_usable_conid_and_skips_snapshot():
    session = SequencedSession([FakeResponse(200, "ok", [{"symbol": "AAPL"}])])
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    quote = client.get_quote("AAPL")

    assert quote is None
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL"}, "json": None, "timeout": 10.0, "verify": False},
        )
    ]


def test_get_historical_data_maps_history_payload():
    session = SequencedSession(
        [
            FakeResponse(200, "ok", [{"conid": 265598, "symbol": "AAPL"}]),
            FakeResponse(
                200,
                "ok",
                {"data": [{"t": 1704153600000, "o": 10.0, "h": 12.0, "l": 9.0, "c": 11.0, "v": 1000}]},
            ),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    bars = client.get_historical_data("AAPL", duration="1 W", bar_size="1 day")

    assert bars == [{"date": 1704153600000, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000}]
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL"}, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/marketdata/history",
            {"params": {"conid": 265598, "period": "1w", "bar": "1d"}, "json": None, "timeout": 10.0, "verify": False},
        ),
    ]


def test_get_historical_data_returns_empty_when_search_result_has_no_usable_conid_and_skips_history():
    session = SequencedSession([FakeResponse(200, "ok", [{"conid": ""}])])
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    bars = client.get_historical_data("AAPL")

    assert bars == []
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL"}, "json": None, "timeout": 10.0, "verify": False},
        )
    ]


def test_get_fundamentals_returns_partial_rest_fields_and_na_for_missing_values():
    session = SequencedSession(
        [
            FakeResponse(
                200,
                "ok",
                [{"conid": 265598, "symbol": "AAPL", "companyName": "APPLE INC"}],
            ),
            FakeResponse(
                200,
                "ok",
                {
                    "companyName": "APPLE INC",
                    "industry": "Technology",
                    "sectorGroup": "Hardware",
                },
            ),
            FakeResponse(
                200,
                "ok",
                [{"conid": 265598, "7289": "190.0", "7290": "165.0", "7282": "1200000"}],
            ),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    data = client.get_fundamentals("AAPL")

    assert data == ibkr_rest_module.FundamentalData(
        conid=265598,
        symbol="AAPL",
        company_name="APPLE INC",
        industry="Technology",
        category="Hardware",
        market_cap="N/A",
        pe_ratio="N/A",
        eps="N/A",
        dividend_yield="N/A",
        high_52w="190.0",
        low_52w="165.0",
        avg_volume="1200000",
    )
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL"}, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/contract/265598/info",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/marketdata/snapshot",
            {
                "params": {"conids": 265598, "fields": "7282,7289,7290"},
                "json": None,
                "timeout": 10.0,
                "verify": False,
            },
        ),
    ]


def test_get_fundamentals_returns_partial_data_when_snapshot_request_fails():
    session = SequencedSession(
        [
            FakeResponse(
                200,
                "ok",
                [{"conid": 265598, "symbol": "AAPL", "companyName": "APPLE INC"}],
            ),
            FakeResponse(
                200,
                "ok",
                {
                    "companyName": "APPLE INC",
                    "industry": "Technology",
                    "sectorGroup": "Hardware",
                },
            ),
            FakeResponse(500, "snapshot failed", {"error": "snapshot failed"}),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    data = client.get_fundamentals("AAPL")

    assert data == ibkr_rest_module.FundamentalData(
        conid=265598,
        symbol="AAPL",
        company_name="APPLE INC",
        industry="Technology",
        category="Hardware",
        market_cap="N/A",
        pe_ratio="N/A",
        eps="N/A",
        dividend_yield="N/A",
        high_52w="N/A",
        low_52w="N/A",
        avg_volume="N/A",
    )
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL"}, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/contract/265598/info",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/marketdata/snapshot",
            {
                "params": {"conids": 265598, "fields": "7282,7289,7290"},
                "json": None,
                "timeout": 10.0,
                "verify": False,
            },
        ),
    ]


def test_run_scanner_maps_ranked_results():
    session = SequencedSession(
        [
            FakeResponse(
                200,
                "ok",
                [
                    {
                        "rank": 1,
                        "symbol": "AAPL",
                        "conid": 265598,
                        "distance": "2.5",
                        "benchmark": "0.0",
                        "projection": "1.2",
                    }
                ],
            ),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    rows = client.run_scanner(scan_type="TOP_PERC_GAIN", size=5)

    assert rows == [
        {
            "rank": 1,
            "symbol": "AAPL",
            "conid": 265598,
            "distance": "2.5",
            "benchmark": "0.0",
            "projection": "1.2",
        }
    ]
    assert session.calls == [
        (
            "POST",
            "https://localhost:5000/v1/api/iserver/scanner/run",
            {
                "params": None,
                "json": {
                    "instrument": "STK",
                    "type": "TOP_PERC_GAIN",
                    "location": "STK.US.MAJOR",
                    "size": "5",
                },
                "timeout": 10.0,
                "verify": False,
            },
        )
    ]


def test_get_company_news_rejects_oversized_response(monkeypatch):
    class FakeNewsResponse:
        status_code = 200
        text = "x" * (ibkr_rest_module.NEWS_MAX_RESPONSE_BYTES + 1)
        content = text.encode("utf-8")

    monkeypatch.setattr(
        ibkr_rest_module.requests,
        "get",
        lambda *args, **kwargs: FakeNewsResponse(),
    )
    client = ibkr_rest_module.IBKRRESTTradingClient()

    assert client.get_company_news("LMND") == []


def test_get_company_news_parses_rss_items_and_applies_limit(monkeypatch):
    xml_text = """
    <rss>
      <channel>
        <item><title>A</title><pubDate>D1</pubDate><link>https://example.com/a</link></item>
        <item><title>B</title><pubDate>D2</pubDate><link>https://example.com/b</link></item>
      </channel>
    </rss>
    """

    class FakeNewsResponse:
        status_code = 200
        text = xml_text
        content = xml_text.encode("utf-8")

    monkeypatch.setattr(
        ibkr_rest_module.requests,
        "get",
        lambda *args, **kwargs: FakeNewsResponse(),
    )
    client = ibkr_rest_module.IBKRRESTTradingClient()

    news = client.get_company_news("AAPL", limit=1)

    assert news == [{"title": "A", "date": "D1", "link": "https://example.com/a"}]


def test_place_order_confirms_reply_then_returns_trade_snapshot():
    session = SequencedSession(
        [
            FakeResponse(200, "ok", [{"conid": 265598, "symbol": "AAPL"}]),
            FakeResponse(200, "ok", [{"id": "reply-1"}]),
            FakeResponse(
                200,
                "ok",
                [
                    {
                        "orderId": 9001,
                        "permId": 8001,
                        "ticker": "AAPL",
                        "secType": "STK",
                        "side": "BUY",
                        "orderType": "LMT",
                        "totalSize": "10",
                        "price": "180.5",
                        "status": "Submitted",
                        "filledQuantity": "0",
                        "remainingQuantity": "10",
                        "avgPrice": "0",
                        "lastExecutionPrice": "0",
                        "listingExchange": "SMART",
                        "acct": "DU111",
                        "lastExecutionTime": "2026-04-19T10:00:00Z",
                        "execution": [],
                    }
                ],
            ),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(
        default_account_id="DU111",
        session_factory=lambda: session,
    )
    request = ibkr_rest_module.OrderRequest(
        contract=ibkr_rest_module.ContractSpec(sec_type="STK", symbol="AAPL"),
        action="BUY",
        quantity=10,
        order_type="LMT",
        limit_price=180.5,
        tif="DAY",
        outside_rth=True,
    )

    snapshot = client.place_order(request)

    assert snapshot == ibkr_rest_module.TradeSnapshot(
        order=ibkr_rest_module.OrderSnapshot(
            order_id=9001,
            perm_id=8001,
            symbol="AAPL",
            sec_type="STK",
            action="BUY",
            order_type="LMT",
            total_quantity=10.0,
            limit_price=180.5,
            stop_price=None,
            status="Submitted",
            filled=0.0,
            remaining=10.0,
            avg_fill_price=0.0,
            last_fill_price=0.0,
            exchange="SMART",
            account="DU111",
            time="2026-04-19T10:00:00Z",
        ),
        fills=[],
    )
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/secdef/search",
            {"params": {"symbol": "AAPL"}, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "POST",
            "https://localhost:5000/v1/api/iserver/account/DU111/orders",
            {
                "params": None,
                "json": {
                    "orders": [
                        {
                            "conid": 265598,
                            "side": "BUY",
                            "quantity": 10,
                            "orderType": "LMT",
                            "price": 180.5,
                            "tif": "DAY",
                            "outsideRTH": True,
                        }
                    ]
                },
                "timeout": 10.0,
                "verify": False,
            },
        ),
        (
            "POST",
            "https://localhost:5000/v1/api/iserver/reply/reply-1",
            {"params": None, "json": {"confirmed": True}, "timeout": 10.0, "verify": False},
        ),
    ]


def test_cancel_modify_and_query_methods_map_rest_payloads():
    trade_row = {
        "orderId": 9001,
        "permId": 8001,
        "ticker": "AAPL",
        "secType": "STK",
        "side": "SELL",
        "orderType": "STOP_LIMIT",
        "totalSize": "12",
        "price": "181.0",
        "auxPrice": "179.0",
        "status": "Submitted",
        "filledQuantity": "5",
        "remainingQuantity": "7",
        "avgPrice": "180.2",
        "lastExecutionPrice": "180.1",
        "listingExchange": "SMART",
        "acct": "DU111",
        "lastExecutionTime": "2026-04-19T10:05:00Z",
        "execution": [
            {
                "execId": "E-1",
                "time": "2026-04-19T10:04:00Z",
                "price": "180.1",
                "shares": "5",
                "exchange": "NYSE",
            }
        ],
    }
    open_row = {
        "orderId": 9002,
        "permId": 8002,
        "ticker": "TSLA",
        "secType": "STK",
        "side": "BUY",
        "orderType": "MKT",
        "totalSize": "2",
        "status": "PreSubmitted",
        "filledQuantity": "0",
        "remainingQuantity": "2",
        "listingExchange": "SMART",
        "acct": "DU111",
        "lastExecutionTime": "2026-04-19T10:06:00Z",
    }
    session = SequencedSession(
        [
            FakeResponse(200, "ok", {"order_id": 9001, "status": "cancelled"}),
            FakeResponse(200, "ok", [trade_row]),
            FakeResponse(200, "ok", {"orders": [open_row]}),
            FakeResponse(200, "ok", [open_row]),
            FakeResponse(200, "ok", [trade_row]),
            FakeResponse(200, "ok", [trade_row]),
        ]
    )
    client = ibkr_rest_module.IBKRRESTTradingClient(
        default_account_id="DU111",
        session_factory=lambda: session,
    )

    cancel_payload = client.cancel_order(9001)
    modified = client.modify_order(
        ibkr_rest_module.ModifyOrderRequest(
            order_id=9001,
            quantity=12,
            limit_price=181.0,
            stop_price=179.0,
            tif="GTC",
            outside_rth=False,
        )
    )
    open_orders = client.get_open_orders()
    orders = client.get_orders()
    trades = client.get_trades()
    fills = client.get_fills()

    assert cancel_payload == {"order_id": 9001, "status": "cancelled"}
    assert modified.order.order_type == "STP_LMT"
    assert modified.order.action == "SELL"
    assert modified.order.limit_price == 181.0
    assert modified.order.stop_price == 179.0
    assert len(modified.fills) == 1
    assert open_orders[0].order_id == 9002
    assert orders[0].symbol == "TSLA"
    assert trades[0].order.order_id == 9001
    assert fills == [
        ibkr_rest_module.FillSnapshot(
            execution_id="E-1",
            time="2026-04-19T10:04:00Z",
            price=180.1,
            quantity=5.0,
            exchange="NYSE",
        )
    ]
    with pytest.raises(ValueError):
        client._build_order_payload(
            ibkr_rest_module.OrderRequest(
                contract=ibkr_rest_module.ContractSpec(sec_type="STK", symbol="AAPL", con_id=0),
                action="BUY",
                quantity=1,
                order_type="MKT",
            )
        )
    with pytest.raises(ValueError):
        client._build_order_payload(
            ibkr_rest_module.OrderRequest(
                contract=ibkr_rest_module.ContractSpec(sec_type="STK", symbol="AAPL", con_id=1),
                action="HOLD",
                quantity=1,
                order_type="MKT",
            )
        )
    with pytest.raises(ValueError):
        client._build_order_payload(
            ibkr_rest_module.OrderRequest(
                contract=ibkr_rest_module.ContractSpec(sec_type="STK", symbol="AAPL", con_id=1),
                action="BUY",
                quantity=1,
                order_type="VWAP",
            )
        )
    assert session.calls == [
        (
            "DELETE",
            "https://localhost:5000/v1/api/iserver/account/DU111/order/9001",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "POST",
            "https://localhost:5000/v1/api/iserver/account/DU111/order/9001",
            {
                "params": None,
                "json": {
                    "quantity": 12,
                    "orderType": "STOP_LIMIT",
                    "price": 181.0,
                    "auxPrice": 179.0,
                    "tif": "GTC",
                    "outsideRTH": False,
                },
                "timeout": 10.0,
                "verify": False,
            },
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/account/orders",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/account/orders",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/account/trades",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
        (
            "GET",
            "https://localhost:5000/v1/api/iserver/account/trades",
            {"params": None, "json": None, "timeout": 10.0, "verify": False},
        ),
    ]

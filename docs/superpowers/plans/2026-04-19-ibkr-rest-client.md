# IBKR REST Trading Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new `scripts/ibkr_rest_trading.py` client plus a `scripts/compare_ibkr_clients.py` comparison tool that reproduce the current trading script’s core read/write workflows through IBKR Client Portal Gateway REST APIs and make the socket-vs-REST differences explicit.

**Architecture:** Keep the existing socket implementation untouched and add a separate REST client with nearly identical public dataclasses and method names. The REST client will use `requests.Session` with injected session creation, explicit JSON request helpers, explicit order-reply confirmation handling, and explicit `N/A` for unsupported fundamentals fields. A separate comparison script will instantiate both clients, run the same operations, and render structured diffs instead of hiding mismatches.

**Tech Stack:** Python 3.11, `requests`, `pytest`, `dataclasses`, `argparse`, `xml.etree.ElementTree`, GitHub Actions

---

## File Responsibility Map

- `scripts/ibkr_rest_trading.py`
  - New REST trading client
  - Owns REST session lifecycle, endpoint calls, JSON parsing, order confirmation flow, news RSS reuse, and REST-to-snapshot normalization
- `tests/test_ibkr_rest_trading.py`
  - New unit tests for the REST client
  - Owns fake HTTP session/response helpers and all REST behavior verification
- `scripts/compare_ibkr_clients.py`
  - New comparison runner for socket vs REST side-by-side checks
  - Owns structured diff generation and CLI entrypoint
- `.github/workflows/python-tests.yml`
  - Add REST tests and compile checks
- `README.md`
  - Add a short section showing how to run the REST client and comparison script

---

### Task 1: Create the REST client foundation and request helpers

**Files:**
- Create: `scripts/ibkr_rest_trading.py`
- Create: `tests/test_ibkr_rest_trading.py`
- Test: `tests/test_ibkr_rest_trading.py`

- [ ] **Step 1: Write the failing tests for constructor injection and HTTP error visibility**

```python
from importlib import util
from pathlib import Path

import pytest


def _load_ibkr_rest_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ibkr_rest_trading.py"
    spec = util.spec_from_file_location("ibkr_rest_trading", script_path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code, payload, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.routes = {}

    def request(self, method, url, *, params=None, json=None, timeout=None, verify=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "timeout": timeout,
                "verify": verify,
            }
        )
        key = (method.upper(), url)
        return self.routes[key]

    def close(self):
        self.closed = True


def test_rest_client_uses_injected_session_factory():
    module = _load_ibkr_rest_module()
    session = FakeSession()

    client = module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api",
        session_factory=lambda: session,
    )

    assert client.base_url == "https://localhost:5000/v1/api"
    assert client.session is session
    assert client.default_account_id is None


def test_request_json_raises_with_method_path_and_status():
    module = _load_ibkr_rest_module()
    session = FakeSession()
    session.routes[("GET", "https://localhost:5000/v1/api/failure")] = FakeResponse(
        503,
        {"error": "gateway not ready"},
        text='{"error":"gateway not ready"}',
    )
    client = module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api",
        session_factory=lambda: session,
    )

    with pytest.raises(RuntimeError) as exc:
        client._request_json("GET", "/failure")

    assert "GET /failure failed" in str(exc.value)
    assert "503" in str(exc.value)
    assert "gateway not ready" in str(exc.value)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_rest_client_uses_injected_session_factory tests/test_ibkr_rest_trading.py::test_request_json_raises_with_method_path_and_status -v`

Expected: FAIL with `FileNotFoundError`, `ModuleNotFoundError`, or `AttributeError` because `scripts/ibkr_rest_trading.py` does not exist yet.

- [ ] **Step 3: Write the minimal REST client skeleton and request helper**

```python
#!/usr/bin/env python3
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import requests

DEFAULT_BASE_URL = os.getenv("IBKR_REST_BASE_URL", "https://localhost:5000/v1/api")
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("IBKR_REST_TIMEOUT_SECONDS", "10"))
DEFAULT_VERIFY_SSL = os.getenv("IBKR_REST_VERIFY_SSL", "false").lower() == "true"


@dataclass
class Position:
    symbol: str
    conid: int
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    pnl_percent: float


@dataclass
class Quote:
    conid: int
    symbol: str
    last_price: float
    bid: float
    ask: float
    volume: int
    change: float
    change_pct: float


@dataclass
class FundamentalData:
    conid: int
    symbol: str
    company_name: str
    industry: str
    category: str
    market_cap: str
    pe_ratio: str
    eps: str
    dividend_yield: str
    high_52w: str
    low_52w: str
    avg_volume: str


@dataclass(frozen=True)
class ContractSpec:
    sec_type: str
    symbol: str
    exchange: Optional[str] = None
    currency: Optional[str] = None
    primary_exchange: Optional[str] = None
    last_trade_date_or_contract_month: Optional[str] = None
    strike: Optional[float] = None
    right: Optional[str] = None
    multiplier: Optional[str] = None
    local_symbol: Optional[str] = None
    trading_class: Optional[str] = None
    con_id: Optional[int] = None


@dataclass(frozen=True)
class OrderRequest:
    contract: ContractSpec
    action: str
    quantity: float
    order_type: str
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: Optional[str] = None
    outside_rth: Optional[bool] = None
    account: Optional[str] = None
    transmit: Optional[bool] = None


@dataclass(frozen=True)
class ModifyOrderRequest:
    order_id: int
    quantity: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: Optional[str] = None
    outside_rth: Optional[bool] = None
    transmit: Optional[bool] = None


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: Optional[int]
    perm_id: Optional[int]
    symbol: Optional[str]
    sec_type: Optional[str]
    action: Optional[str]
    order_type: Optional[str]
    total_quantity: Optional[float]
    limit_price: Optional[float]
    stop_price: Optional[float]
    status: Optional[str]
    filled: Optional[float]
    remaining: Optional[float]
    avg_fill_price: Optional[float]
    last_fill_price: Optional[float]
    exchange: Optional[str]
    account: Optional[str]
    time: Optional[str]


@dataclass(frozen=True)
class FillSnapshot:
    execution_id: Optional[str]
    time: Optional[str]
    price: Optional[float]
    quantity: Optional[float]
    exchange: Optional[str]


@dataclass(frozen=True)
class TradeSnapshot:
    order: OrderSnapshot
    fills: List[FillSnapshot]


class IBKRRESTTradingClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        session_factory: Callable[[], Any] = requests.Session,
        verify_ssl: bool = DEFAULT_VERIFY_SSL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        default_account_id: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = session_factory()
        self.verify_ssl = verify_ssl
        self.timeout_seconds = timeout_seconds
        self.default_account_id = default_account_id
        self._authenticated = False

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        response = self.session.request(
            method.upper(),
            f"{self.base_url}{path}",
            params=params,
            json=payload,
            timeout=self.timeout_seconds,
            verify=self.verify_ssl,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"{method.upper()} {path} failed: {response.status_code} {response.text}")
        return response.json()

    def disconnect(self) -> None:
        self.session.close()
```

- [ ] **Step 4: Run the tests to verify it passes**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_rest_client_uses_injected_session_factory tests/test_ibkr_rest_trading.py::test_request_json_raises_with_method_path_and_status -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ibkr_rest_trading.py tests/test_ibkr_rest_trading.py
git commit -m "feat: add REST client foundation"
```

### Task 2: Implement authentication, account discovery, balance, and paginated positions

**Files:**
- Modify: `scripts/ibkr_rest_trading.py`
- Modify: `tests/test_ibkr_rest_trading.py`
- Test: `tests/test_ibkr_rest_trading.py`

- [ ] **Step 1: Write the failing tests for auth status, account selection, balance mapping, and paginated positions**

```python
def test_connect_authenticates_and_sets_default_account():
    module = _load_ibkr_rest_module()
    session = FakeSession()
    base = "https://localhost:5000/v1/api"
    session.routes[("GET", f"{base}/iserver/auth/status")] = FakeResponse(
        200,
        {"authenticated": True, "connected": True, "competing": False},
    )
    session.routes[("GET", f"{base}/portfolio/accounts")] = FakeResponse(
        200,
        [{"accountId": "DU1234567", "id": "DU1234567"}],
    )
    session.routes[("POST", f"{base}/tickle")] = FakeResponse(200, {"session": "ok"})
    client = module.IBKRRESTTradingClient(base_url=base, session_factory=lambda: session)

    assert client.connect() is True
    assert client.is_authenticated() is True
    assert client.default_account_id == "DU1234567"


def test_get_balance_keeps_duplicate_tags_and_get_positions_aggregates_pages():
    module = _load_ibkr_rest_module()
    session = FakeSession()
    base = "https://localhost:5000/v1/api"
    session.routes[("GET", f"{base}/portfolio/DU1234567/summary")] = FakeResponse(
        200,
        [
            {"tag": "NetLiquidation", "amount": 1000.0, "currency": "USD", "acctId": "DU1234567"},
            {"tag": "NetLiquidation", "amount": 2000.0, "currency": "HKD", "acctId": "DU1234567"},
            {"tag": "TotalCashValue", "amount": 400.0, "currency": "USD", "acctId": "DU1234567"},
        ],
    )
    session.routes[("GET", f"{base}/portfolio/DU1234567/ledger")] = FakeResponse(
        200,
        {"USD": {"cashbalance": 400.0}, "HKD": {"cashbalance": 0.0}},
    )
    session.routes[("GET", f"{base}/portfolio/DU1234567/positions/0")] = FakeResponse(
        200,
        [
            {
                "conid": 265598,
                "contractDesc": "AAPL",
                "position": 10,
                "avgCost": 150.0,
                "mktValue": 1755.0,
                "unrealizedPnl": 255.0,
            }
        ],
    )
    session.routes[("GET", f"{base}/portfolio/DU1234567/positions/1")] = FakeResponse(200, [])
    client = module.IBKRRESTTradingClient(
        base_url=base,
        session_factory=lambda: session,
        default_account_id="DU1234567",
    )

    balance = client.get_balance()
    positions = client.get_positions()

    assert balance["NetLiquidation"] == [
        {"amount": 1000.0, "currency": "USD", "account": "DU1234567"},
        {"amount": 2000.0, "currency": "HKD", "account": "DU1234567"},
    ]
    assert balance["TotalCashValue"] == [
        {"amount": 400.0, "currency": "USD", "account": "DU1234567"}
    ]
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].conid == 265598
    assert positions[0].pnl_percent == 17.0
```

- [ ] **Step 2: Run the tests to verify it fails**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_connect_authenticates_and_sets_default_account tests/test_ibkr_rest_trading.py::test_get_balance_keeps_duplicate_tags_and_get_positions_aggregates_pages -v`

Expected: FAIL with missing `connect`, `is_authenticated`, `get_balance`, or `get_positions` methods.

- [ ] **Step 3: Write the minimal authentication and portfolio implementation**

```python
def _require_account_id(self, account_id: Optional[str] = None) -> str:
    resolved = account_id or self.default_account_id
    if not resolved:
        raise ValueError("account_id is required")
    return resolved


def connect(self) -> bool:
    status = self._request_json("GET", "/iserver/auth/status")
    self._authenticated = bool(status.get("authenticated"))
    if not self._authenticated:
        return False
    self._request_json("POST", "/tickle")
    accounts = self.get_accounts()
    if accounts and self.default_account_id is None:
        first = accounts[0]
        self.default_account_id = first.get("accountId") or first.get("id")
    return True


def is_authenticated(self) -> bool:
    return self._authenticated


def get_accounts(self) -> List[Dict[str, Any]]:
    return self._request_json("GET", "/portfolio/accounts")


def get_balance(self, account_id: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    resolved = self._require_account_id(account_id)
    summary = self._request_json("GET", f"/portfolio/{resolved}/summary")
    self._request_json("GET", f"/portfolio/{resolved}/ledger")
    result: Dict[str, List[Dict[str, Any]]] = {}
    for item in summary:
        entries = result.setdefault(item["tag"], [])
        entries.append(
            {
                "amount": item.get("amount"),
                "currency": item.get("currency"),
                "account": item.get("acctId") or resolved,
            }
        )
    return result


def get_positions(self, account_id: Optional[str] = None) -> List[Position]:
    resolved = self._require_account_id(account_id)
    page = 0
    positions: List[Position] = []
    while True:
        payload = self._request_json("GET", f"/portfolio/{resolved}/positions/{page}")
        if not payload:
            return positions
        for item in payload:
            quantity = float(item.get("position", 0) or 0)
            avg_cost = float(item.get("avgCost", 0) or 0)
            market_value = float(item.get("mktValue", 0) or 0)
            unrealized_pnl = float(item.get("unrealizedPnl", 0) or 0)
            cost_basis = avg_cost * quantity if quantity else 0.0
            pnl_percent = round(unrealized_pnl / abs(cost_basis) * 100, 2) if cost_basis else 0.0
            positions.append(
                Position(
                    symbol=item.get("contractDesc") or item.get("ticker") or "",
                    conid=int(item.get("conid", 0) or 0),
                    quantity=quantity,
                    avg_cost=avg_cost,
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                    pnl_percent=pnl_percent,
                )
            )
        page += 1
```

- [ ] **Step 4: Run the tests to verify it passes**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_connect_authenticates_and_sets_default_account tests/test_ibkr_rest_trading.py::test_get_balance_keeps_duplicate_tags_and_get_positions_aggregates_pages -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ibkr_rest_trading.py tests/test_ibkr_rest_trading.py
git commit -m "feat: add REST auth and portfolio support"
```

### Task 3: Implement symbol search, quote snapshots, and historical data

**Files:**
- Modify: `scripts/ibkr_rest_trading.py`
- Modify: `tests/test_ibkr_rest_trading.py`
- Test: `tests/test_ibkr_rest_trading.py`

- [ ] **Step 1: Write the failing tests for symbol lookup, quote mapping, and historical bars**

```python
def test_search_symbol_get_quote_and_get_historical_data_map_rest_payloads():
    module = _load_ibkr_rest_module()
    session = FakeSession()
    base = "https://localhost:5000/v1/api"
    session.routes[("GET", f"{base}/iserver/secdef/search")] = FakeResponse(
        200,
        [
            {
                "conid": 265598,
                "symbol": "AAPL",
                "companyName": "APPLE INC",
                "description": "NASDAQ",
            }
        ],
    )
    session.routes[("GET", f"{base}/iserver/marketdata/snapshot")] = FakeResponse(
        200,
        [
            {
                "conid": 265598,
                "31": "101.5",
                "84": "101.0",
                "86": "102.0",
                "87": "1500",
                "88": "100.0",
                "7762": "1.5",
            }
        ],
    )
    session.routes[("GET", f"{base}/iserver/marketdata/history")] = FakeResponse(
        200,
        {
            "data": [
                {"t": 1704153600000, "o": 10.0, "h": 12.0, "l": 9.0, "c": 11.0, "v": 1000}
            ]
        },
    )
    client = module.IBKRRESTTradingClient(base_url=base, session_factory=lambda: session)

    contract = client.search_symbol("AAPL")
    quote = client.get_quote("AAPL")
    bars = client.get_historical_data("AAPL", duration="1 W", bar_size="1 day")

    assert contract["conid"] == 265598
    assert quote.conid == 265598
    assert quote.symbol == "AAPL"
    assert quote.last_price == 101.5
    assert quote.bid == 101.0
    assert quote.ask == 102.0
    assert quote.volume == 1500
    assert quote.change == 1.5
    assert quote.change_pct == 1.5
    assert bars == [
        {"date": 1704153600000, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000}
    ]
```

- [ ] **Step 2: Run the tests to verify it fails**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_search_symbol_get_quote_and_get_historical_data_map_rest_payloads -v`

Expected: FAIL with missing `search_symbol`, `get_quote`, or `get_historical_data`.

- [ ] **Step 3: Write the minimal search, quote, and history implementation**

```python
QUOTE_FIELDS = "31,84,86,87,88,7762"
HISTORY_PERIOD_MAP = {
    "1 D": "1d",
    "1 W": "1w",
    "1 M": "1m",
    "3 M": "3m",
    "6 M": "6m",
    "1 Y": "1y",
    "5 Y": "5y",
}
HISTORY_BAR_MAP = {
    "1 min": "1min",
    "5 mins": "5min",
    "1 hour": "1h",
    "1 day": "1d",
    "1 week": "1w",
    "1 month": "1m",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    return float(value)


def search_symbol(self, symbol: str, **_: Any) -> Optional[Dict[str, Any]]:
    results = self._request_json("GET", "/iserver/secdef/search", params={"symbol": symbol})
    return results[0] if results else None


def get_quote(self, symbol: str) -> Optional[Quote]:
    contract = self.search_symbol(symbol)
    if contract is None:
        return None
    payload = self._request_json(
        "GET",
        "/iserver/marketdata/snapshot",
        params={"conids": contract["conid"], "fields": QUOTE_FIELDS},
    )
    item = payload[0]
    last_price = _to_float(item.get("31"))
    close_price = _to_float(item.get("88"))
    change = round(last_price - close_price, 2) if close_price else 0.0
    change_pct = round(_to_float(item.get("7762"), change / close_price * 100 if close_price else 0.0), 2)
    return Quote(
        conid=int(contract["conid"]),
        symbol=symbol,
        last_price=last_price,
        bid=_to_float(item.get("84")),
        ask=_to_float(item.get("86")),
        volume=int(_to_float(item.get("87"))),
        change=change,
        change_pct=change_pct,
    )


def get_historical_data(self, symbol: str, duration: str = "3 M", bar_size: str = "1 day") -> List[dict]:
    contract = self.search_symbol(symbol)
    if contract is None:
        return []
    payload = self._request_json(
        "GET",
        "/iserver/marketdata/history",
        params={
            "conid": contract["conid"],
            "period": HISTORY_PERIOD_MAP[duration],
            "bar": HISTORY_BAR_MAP[bar_size],
        },
    )
    return [
        {
            "date": bar["t"],
            "open": bar["o"],
            "high": bar["h"],
            "low": bar["l"],
            "close": bar["c"],
            "volume": bar["v"],
        }
        for bar in payload.get("data", [])
    ]
```

- [ ] **Step 4: Run the tests to verify it passes**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_search_symbol_get_quote_and_get_historical_data_map_rest_payloads -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ibkr_rest_trading.py tests/test_ibkr_rest_trading.py
git commit -m "feat: add REST market data support"
```

### Task 4: Implement partial fundamentals, scanner, and Yahoo RSS news parity

**Files:**
- Modify: `scripts/ibkr_rest_trading.py`
- Modify: `tests/test_ibkr_rest_trading.py`
- Test: `tests/test_ibkr_rest_trading.py`

- [ ] **Step 1: Write the failing tests for fundamentals `N/A` behavior, scanner results, and RSS request handling**

```python
def test_get_fundamentals_returns_partial_rest_fields_and_na_for_missing_values(monkeypatch):
    module = _load_ibkr_rest_module()
    session = FakeSession()
    base = "https://localhost:5000/v1/api"
    session.routes[("GET", f"{base}/iserver/secdef/search")] = FakeResponse(
        200,
        [{"conid": 265598, "symbol": "AAPL", "companyName": "APPLE INC", "description": "NASDAQ"}],
    )
    session.routes[("GET", f"{base}/iserver/contract/265598/info")] = FakeResponse(
        200,
        {"companyName": "APPLE INC", "industry": "Technology", "sectorGroup": "Hardware"},
    )
    session.routes[("GET", f"{base}/iserver/marketdata/snapshot")] = FakeResponse(
        200,
        [{"conid": 265598, "7289": "190.0", "7290": "165.0", "7282": "1200000"}],
    )
    client = module.IBKRRESTTradingClient(base_url=base, session_factory=lambda: session)

    data = client.get_fundamentals("AAPL")

    assert data.company_name == "APPLE INC"
    assert data.industry == "Technology"
    assert data.category == "Hardware"
    assert data.high_52w == "190.0"
    assert data.low_52w == "165.0"
    assert data.avg_volume == "1200000"
    assert data.market_cap == "N/A"
    assert data.pe_ratio == "N/A"
    assert data.eps == "N/A"


def test_run_scanner_maps_ranked_results():
    module = _load_ibkr_rest_module()
    session = FakeSession()
    base = "https://localhost:5000/v1/api"
    session.routes[("POST", f"{base}/iserver/scanner/run")] = FakeResponse(
        200,
        [
            {"rank": 1, "symbol": "AAPL", "conid": 265598, "distance": "2.5", "benchmark": "0.0", "projection": "1.2"}
        ],
    )
    client = module.IBKRRESTTradingClient(base_url=base, session_factory=lambda: session)

    rows = client.run_scanner(scan_type="TOP_PERC_GAIN", size=5)

    assert rows == [
        {"rank": 1, "symbol": "AAPL", "conid": 265598, "distance": "2.5", "benchmark": "0.0", "projection": "1.2"}
    ]


def test_get_company_news_rejects_oversized_response(monkeypatch):
    module = _load_ibkr_rest_module()

    class FakeNewsResponse:
        status_code = 200
        text = "x" * (module.NEWS_MAX_RESPONSE_BYTES + 1)
        content = text.encode("utf-8")

    monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: FakeNewsResponse())
    client = module.IBKRRESTTradingClient()

    assert client.get_company_news("LMND") == []
```

- [ ] **Step 2: Run the tests to verify it fails**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_get_fundamentals_returns_partial_rest_fields_and_na_for_missing_values tests/test_ibkr_rest_trading.py::test_run_scanner_maps_ranked_results tests/test_ibkr_rest_trading.py::test_get_company_news_rejects_oversized_response -v`

Expected: FAIL with missing `get_fundamentals`, `run_scanner`, `get_company_news`, or missing constants.

- [ ] **Step 3: Write the minimal fundamentals, scanner, and news implementation**

```python
import sys
import xml.etree.ElementTree as ET

NEWS_REQUEST_TIMEOUT_SECONDS = 10
NEWS_USER_AGENT = "Mozilla/5.0"
NEWS_MAX_RESPONSE_BYTES = 1_000_000
FUNDAMENTAL_SNAPSHOT_FIELDS = "7282,7289,7290"


def log_warning(context: str, error: Exception) -> None:
    print(f"{context} 发生错误: {error}", file=sys.stderr)


def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]:
    contract = self.search_symbol(symbol)
    if contract is None:
        return None
    conid = int(contract["conid"])
    info = self._request_json("GET", f"/iserver/contract/{conid}/info")
    snapshot = self._request_json(
        "GET",
        "/iserver/marketdata/snapshot",
        params={"conids": conid, "fields": FUNDAMENTAL_SNAPSHOT_FIELDS},
    )
    market = snapshot[0] if snapshot else {}
    return FundamentalData(
        conid=conid,
        symbol=symbol,
        company_name=info.get("companyName") or contract.get("companyName") or symbol,
        industry=info.get("industry") or "",
        category=info.get("sectorGroup") or info.get("category") or "",
        market_cap="N/A",
        pe_ratio="N/A",
        eps="N/A",
        dividend_yield=info.get("dividendYield") or "N/A",
        high_52w=str(market.get("7289") or "N/A"),
        low_52w=str(market.get("7290") or "N/A"),
        avg_volume=str(market.get("7282") or "N/A"),
    )


def run_scanner(self, scan_type: str = "TOP_PERC_GAIN", size: int = 10) -> List[dict]:
    payload = {
        "instrument": "STK",
        "type": scan_type,
        "location": "STK.US.MAJOR",
        "size": str(size),
    }
    rows = self._request_json("POST", "/iserver/scanner/run", payload=payload)
    return [
        {
            "rank": row.get("rank"),
            "symbol": row.get("symbol"),
            "conid": row.get("conid"),
            "distance": row.get("distance"),
            "benchmark": row.get("benchmark"),
            "projection": row.get("projection"),
        }
        for row in rows
    ]


def get_company_news(self, symbol: str, limit: int = 5) -> List[dict]:
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    response = requests.get(url, headers={"User-Agent": NEWS_USER_AGENT}, timeout=NEWS_REQUEST_TIMEOUT_SECONDS)
    raw_content = getattr(response, "content", None) or response.text.encode("utf-8")
    if response.status_code != 200:
        log_warning(f"get_company_news({symbol})", RuntimeError(f"unexpected status {response.status_code}"))
        return []
    if len(raw_content) > NEWS_MAX_RESPONSE_BYTES:
        log_warning(f"get_company_news({symbol})", RuntimeError("response too large"))
        return []
    root = ET.fromstring(response.text)
    news: List[dict] = []
    for item in root.findall(".//item")[:limit]:
        news.append(
            {
                "title": item.find("title").text if item.find("title") is not None else "",
                "date": item.find("pubDate").text if item.find("pubDate") is not None else "",
                "link": item.find("link").text if item.find("link") is not None else "",
            }
        )
    return news
```

- [ ] **Step 4: Run the tests to verify it passes**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_get_fundamentals_returns_partial_rest_fields_and_na_for_missing_values tests/test_ibkr_rest_trading.py::test_run_scanner_maps_ranked_results tests/test_ibkr_rest_trading.py::test_get_company_news_rejects_oversized_response -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ibkr_rest_trading.py tests/test_ibkr_rest_trading.py
git commit -m "feat: add REST fundamentals scanner and news"
```

### Task 5: Implement order placement, confirmation replies, cancel/modify, and order/trade snapshots

**Files:**
- Modify: `scripts/ibkr_rest_trading.py`
- Modify: `tests/test_ibkr_rest_trading.py`
- Test: `tests/test_ibkr_rest_trading.py`

- [ ] **Step 1: Write the failing tests for order posting, reply confirmation, cancel, modify, and query mapping**

```python
def test_place_order_confirms_reply_then_returns_trade_snapshot():
    module = _load_ibkr_rest_module()
    session = FakeSession()
    base = "https://localhost:5000/v1/api"
    session.routes[("GET", f"{base}/iserver/secdef/search")] = FakeResponse(
        200,
        [{"conid": 265598, "symbol": "AAPL", "companyName": "APPLE INC"}],
    )
    session.routes[("POST", f"{base}/iserver/account/DU1234567/orders")] = FakeResponse(
        200,
        [{"id": "reply-1", "message": ["price exceeds percentage constraint"]}],
    )
    session.routes[("POST", f"{base}/iserver/reply/reply-1")] = FakeResponse(
        200,
        [{"order_id": 77, "order_status": "Submitted", "ticker": "AAPL", "conid": 265598, "side": "BUY", "orderType": "LMT", "total_size": 3, "price": 101.5, "status": "Submitted"}],
    )
    client = module.IBKRRESTTradingClient(
        base_url=base,
        session_factory=lambda: session,
        default_account_id="DU1234567",
    )

    snapshot = client.place_order(
        module.OrderRequest(
            contract=module.ContractSpec(sec_type="STK", symbol="AAPL"),
            action="BUY",
            quantity=3,
            order_type="LMT",
            limit_price=101.5,
            tif="DAY",
        )
    )

    assert snapshot.order.order_id == 77
    assert snapshot.order.symbol == "AAPL"
    assert snapshot.order.action == "BUY"
    assert snapshot.order.order_type == "LMT"
    assert snapshot.order.total_quantity == 3
    assert snapshot.order.limit_price == 101.5


def test_cancel_modify_and_query_methods_map_rest_payloads():
    module = _load_ibkr_rest_module()
    session = FakeSession()
    base = "https://localhost:5000/v1/api"
    session.routes[("DELETE", f"{base}/iserver/account/DU1234567/order/77")] = FakeResponse(200, {"success": True})
    session.routes[("POST", f"{base}/iserver/account/DU1234567/order/77")] = FakeResponse(
        200,
        {"order_id": 77, "status": "Submitted", "ticker": "AAPL", "side": "BUY", "orderType": "LMT", "total_size": 2, "price": 101.7},
    )
    session.routes[("GET", f"{base}/iserver/account/orders")] = FakeResponse(
        200,
        {
            "orders": [
                {"order_id": 77, "ticker": "AAPL", "side": "BUY", "orderType": "LMT", "status": "Submitted", "total_size": 2, "price": 101.7}
            ]
        },
    )
    session.routes[("GET", f"{base}/iserver/account/trades")] = FakeResponse(
        200,
        [
            {"order_id": 77, "ticker": "AAPL", "side": "BUY", "orderType": "LMT", "status": "Submitted", "total_size": 2, "price": 101.7, "executions": [{"execId": "E1", "time": "2026-04-19 09:30:00", "price": 101.7, "shares": 1, "exchange": "NASDAQ"}]}
        ],
    )
    client = module.IBKRRESTTradingClient(
        base_url=base,
        session_factory=lambda: session,
        default_account_id="DU1234567",
    )

    cancel_payload = client.cancel_order(77)
    modified = client.modify_order(module.ModifyOrderRequest(order_id=77, quantity=2, limit_price=101.7))
    open_orders = client.get_open_orders()
    orders = client.get_orders()
    trades = client.get_trades()
    fills = client.get_fills()

    assert cancel_payload == {"success": True}
    assert modified.order.order_id == 77
    assert open_orders[0].order_id == 77
    assert orders[0].order_id == 77
    assert trades[0].order.order_id == 77
    assert fills[0].execution_id == "E1"
```

- [ ] **Step 2: Run the tests to verify it fails**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_place_order_confirms_reply_then_returns_trade_snapshot tests/test_ibkr_rest_trading.py::test_cancel_modify_and_query_methods_map_rest_payloads -v`

Expected: FAIL with missing order methods or incorrect payload mapping.

- [ ] **Step 3: Write the minimal order and query implementation**

```python
SUPPORTED_ORDER_TYPES = {"MKT", "LMT", "STP", "STP_LMT"}
SUPPORTED_ORDER_ACTIONS = {"BUY", "SELL"}


def _resolve_conid(self, spec: ContractSpec) -> int:
    if spec.con_id is not None:
        return int(spec.con_id)
    contract = self.search_symbol(spec.symbol)
    if contract is None:
        raise ValueError(f"unable to resolve contract for {spec.symbol}")
    return int(contract["conid"])


def _build_order_payload(self, request: OrderRequest) -> Dict[str, Any]:
    order_type = request.order_type.upper()
    if order_type not in SUPPORTED_ORDER_TYPES:
        raise ValueError(f"unsupported order_type: {request.order_type}")
    action = request.action.upper()
    if action not in SUPPORTED_ORDER_ACTIONS:
        raise ValueError(f"unsupported action: {request.action}")
    payload = {
        "conid": self._resolve_conid(request.contract),
        "side": action,
        "orderType": "STOP_LIMIT" if order_type == "STP_LMT" else order_type,
        "quantity": request.quantity,
    }
    if request.limit_price is not None:
        payload["price"] = request.limit_price
    if request.stop_price is not None:
        payload["auxPrice"] = request.stop_price
    if request.tif is not None:
        payload["tif"] = request.tif
    if request.outside_rth is not None:
        payload["outsideRTH"] = request.outside_rth
    return payload


def _order_snapshot_from_rest(self, item: Dict[str, Any]) -> OrderSnapshot:
    return OrderSnapshot(
        order_id=item.get("order_id") or item.get("orderId"),
        perm_id=item.get("permId"),
        symbol=item.get("ticker") or item.get("symbol"),
        sec_type=item.get("secType") or "STK",
        action=item.get("side") or item.get("action"),
        order_type=item.get("orderType"),
        total_quantity=item.get("total_size") or item.get("quantity"),
        limit_price=item.get("price"),
        stop_price=item.get("auxPrice"),
        status=item.get("status") or item.get("order_status"),
        filled=item.get("filled"),
        remaining=item.get("remaining"),
        avg_fill_price=item.get("avgFillPrice"),
        last_fill_price=item.get("lastFillPrice"),
        exchange=item.get("listingExchange"),
        account=item.get("acct") or self.default_account_id,
        time=item.get("lastExecutionTime") or item.get("time"),
    )


def _fill_snapshot_from_rest(self, item: Dict[str, Any]) -> FillSnapshot:
    return FillSnapshot(
        execution_id=item.get("execId"),
        time=item.get("time"),
        price=item.get("price"),
        quantity=item.get("shares"),
        exchange=item.get("exchange"),
    )


def _trade_snapshot_from_rest(self, item: Dict[str, Any]) -> TradeSnapshot:
    executions = item.get("executions") or []
    return TradeSnapshot(
        order=self._order_snapshot_from_rest(item),
        fills=[self._fill_snapshot_from_rest(execution) for execution in executions],
    )


def place_order(self, request: OrderRequest) -> TradeSnapshot:
    account_id = request.account or self._require_account_id()
    initial = self._request_json(
        "POST",
        f"/iserver/account/{account_id}/orders",
        payload={"orders": [self._build_order_payload(request)]},
    )
    current = initial
    if isinstance(initial, list) and initial and initial[0].get("id"):
        reply_id = initial[0]["id"]
        current = self._request_json("POST", f"/iserver/reply/{reply_id}", payload={"confirmed": True})
    item = current[0] if isinstance(current, list) else current
    return self._trade_snapshot_from_rest(item)


def cancel_order(self, order_id: int, account_id: Optional[str] = None) -> Dict[str, Any]:
    resolved = self._require_account_id(account_id)
    return self._request_json("DELETE", f"/iserver/account/{resolved}/order/{order_id}")


def modify_order(self, request: ModifyOrderRequest, account_id: Optional[str] = None) -> TradeSnapshot:
    resolved = self._require_account_id(account_id)
    payload: Dict[str, Any] = {}
    if request.quantity is not None:
        payload["quantity"] = request.quantity
    if request.limit_price is not None:
        payload["price"] = request.limit_price
    if request.stop_price is not None:
        payload["auxPrice"] = request.stop_price
    if request.tif is not None:
        payload["tif"] = request.tif
    if request.outside_rth is not None:
        payload["outsideRTH"] = request.outside_rth
    current = self._request_json("POST", f"/iserver/account/{resolved}/order/{request.order_id}", payload=payload)
    item = current[0] if isinstance(current, list) else current
    return self._trade_snapshot_from_rest(item)


def get_open_orders(self) -> List[OrderSnapshot]:
    payload = self._request_json("GET", "/iserver/account/orders")
    rows = payload.get("orders", []) if isinstance(payload, dict) else payload
    return [self._order_snapshot_from_rest(row) for row in rows]


def get_orders(self) -> List[OrderSnapshot]:
    return self.get_open_orders()


def get_trades(self) -> List[TradeSnapshot]:
    payload = self._request_json("GET", "/iserver/account/trades")
    return [self._trade_snapshot_from_rest(row) for row in payload]


def get_fills(self) -> List[FillSnapshot]:
    fills: List[FillSnapshot] = []
    for trade in self.get_trades():
        fills.extend(trade.fills)
    return fills
```

- [ ] **Step 4: Run the tests to verify it passes**

Run: `python3 -m pytest tests/test_ibkr_rest_trading.py::test_place_order_confirms_reply_then_returns_trade_snapshot tests/test_ibkr_rest_trading.py::test_cancel_modify_and_query_methods_map_rest_payloads -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ibkr_rest_trading.py tests/test_ibkr_rest_trading.py
git commit -m "feat: add REST order management"
```

### Task 6: Add the socket-vs-REST comparison script and README usage notes

**Files:**
- Create: `scripts/compare_ibkr_clients.py`
- Modify: `README.md`
- Create: `tests/test_compare_ibkr_clients.py`
- Test: `tests/test_compare_ibkr_clients.py`

- [ ] **Step 1: Write the failing test for structured comparison output**

```python
from importlib import util
from pathlib import Path


def _load_compare_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "compare_ibkr_clients.py"
    spec = util.spec_from_file_location("compare_ibkr_clients", script_path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSocketClient:
    def get_balance(self):
        return {"NetLiquidation": [{"amount": 1000.0, "currency": "USD", "account": "DU123"}]}

    def get_positions(self):
        return [{"symbol": "AAPL", "quantity": 10}]

    def get_quote(self, symbol):
        return {"symbol": symbol, "last_price": 101.5}


class FakeRestClient(FakeSocketClient):
    def get_quote(self, symbol):
        return {"symbol": symbol, "last_price": 101.0}


def test_compare_clients_marks_matching_and_mismatching_sections():
    module = _load_compare_module()
    result = module.compare_clients(
        FakeSocketClient(),
        FakeRestClient(),
        symbol="AAPL",
    )

    assert result["balance"]["match"] is True
    assert result["positions"]["match"] is True
    assert result["quote"]["match"] is False
    assert result["quote"]["socket"]["last_price"] == 101.5
    assert result["quote"]["rest"]["last_price"] == 101.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_compare_ibkr_clients.py::test_compare_clients_marks_matching_and_mismatching_sections -v`

Expected: FAIL with `FileNotFoundError` because `scripts/compare_ibkr_clients.py` does not exist yet.

- [ ] **Step 3: Write the minimal comparison script and README section**

```python
#!/usr/bin/env python3
import argparse
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict

from ibkr_rest_trading import IBKRRESTTradingClient
from ibkr_trading import IBKRTradingClient


def normalize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    return value


def compare_section(socket_value: Any, rest_value: Any) -> Dict[str, Any]:
    socket_normalized = normalize(socket_value)
    rest_normalized = normalize(rest_value)
    return {
        "match": socket_normalized == rest_normalized,
        "socket": socket_normalized,
        "rest": rest_normalized,
    }


def compare_clients(socket_client: Any, rest_client: Any, *, symbol: str) -> Dict[str, Any]:
    return {
        "balance": compare_section(socket_client.get_balance(), rest_client.get_balance()),
        "positions": compare_section(socket_client.get_positions(), rest_client.get_positions()),
        "quote": compare_section(socket_client.get_quote(symbol), rest_client.get_quote(symbol)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="AAPL")
    args = parser.parse_args()

    socket_client = IBKRTradingClient()
    rest_client = IBKRRESTTradingClient()

    if not socket_client.connect():
        raise SystemExit("socket client connect failed")
    if not rest_client.connect():
        raise SystemExit("rest client connect failed")

    try:
        result = compare_clients(socket_client, rest_client, symbol=args.symbol)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        socket_client.disconnect()
        rest_client.disconnect()


if __name__ == "__main__":
    main()
```

```markdown
## REST client quick check

    python3 scripts/compare_ibkr_clients.py --symbol AAPL

This command prints socket-vs-REST results for balance, positions, and quote so you can see which fields still diverge.
```

- [ ] **Step 4: Run the tests to verify it passes**

Run: `python3 -m pytest tests/test_compare_ibkr_clients.py::test_compare_clients_marks_matching_and_mismatching_sections -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/compare_ibkr_clients.py README.md tests/test_compare_ibkr_clients.py
git commit -m "feat: add IBKR client comparison script"
```

### Task 7: Update CI and run full verification

**Files:**
- Modify: `.github/workflows/python-tests.yml`
- Modify: `tests/test_repo_alignment.py`
- Test: `tests/test_repo_alignment.py`

- [ ] **Step 1: Write the failing repository alignment assertions for the new REST files**

```python
def test_ci_workflow_runs_rest_tests_and_compiles_rest_scripts():
    workflow_lines = _normalized_lines(".github/workflows/python-tests.yml")
    workflow_text = "\n".join(workflow_lines)

    assert "python3 -m pytest tests/test_ibkr_rest_trading.py -q" in workflow_text
    assert "python3 -m pytest tests/test_compare_ibkr_clients.py -q" in workflow_text
    assert "python3 -m py_compile scripts/ibkr_rest_trading.py scripts/compare_ibkr_clients.py" in workflow_text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_repo_alignment.py::test_ci_workflow_runs_rest_tests_and_compiles_rest_scripts -v`

Expected: FAIL because the workflow does not mention the new tests or scripts yet.

- [ ] **Step 3: Update the workflow and alignment test, then run the complete verification set**

```yaml
      - run: python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q
        timeout-minutes: 1
      - run: python3 -m pytest tests/test_ibkr_trading.py -q
        timeout-minutes: 1
      - run: python3 -m pytest tests/test_ibkr_rest_trading.py -q
        timeout-minutes: 1
      - run: python3 -m pytest tests/test_compare_ibkr_clients.py -q
        timeout-minutes: 1
      - run: python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py
      - run: python3 -m py_compile scripts/ibkr_trading.py scripts/ibkr_rest_trading.py scripts/compare_ibkr_clients.py
```

Run all verification commands after editing:

```bash
python3 -m pytest tests/test_ibkr_rest_trading.py -q
python3 -m pytest tests/test_compare_ibkr_clients.py -q
python3 -m pytest tests/test_ibkr_trading.py tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q
python3 -m py_compile scripts/ibkr_rest_trading.py scripts/compare_ibkr_clients.py scripts/ibkr_trading.py scripts/ibkr_readonly.py scripts/keepalive.py
```

Expected: all commands PASS

- [ ] **Step 4: Run the real comparison script locally against Client Portal Gateway and the existing socket client**

Run:

```bash
python3 scripts/compare_ibkr_clients.py --symbol AAPL
```

Expected: JSON output containing `balance`, `positions`, and `quote`, each with `match`, `socket`, and `rest` keys. If a section does not match, keep the diff output as evidence; do not replace it with a fake “success”.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/python-tests.yml tests/test_repo_alignment.py
git commit -m "ci: verify REST client and comparison script"
```

---

## Self-Review

**Spec coverage:**
- REST client file: covered by Tasks 1-5
- Comparison script: covered by Task 6
- Local side-by-side test flow: covered by Task 7 Step 4
- CI integration: covered by Task 7
- Partial fundamentals and explicit `N/A`: covered by Task 4
- Order reply confirmation chain: covered by Task 5

**Placeholder scan:**
- No unfinished markers or cross-task references remain.

**Type consistency:**
- `IBKRRESTTradingClient`, `ContractSpec`, `OrderRequest`, `ModifyOrderRequest`, `OrderSnapshot`, `FillSnapshot`, and `TradeSnapshot` are named consistently across all tasks.

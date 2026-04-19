#!/usr/bin/env python3
"""IBKR REST Trading Client."""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import requests


DEFAULT_BASE_URL = os.getenv("IBKR_REST_BASE_URL", "https://localhost:5000/v1/api")
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("IBKR_REST_TIMEOUT_SECONDS", "10"))
DEFAULT_VERIFY_SSL = os.getenv("IBKR_REST_VERIFY_SSL", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
QUOTE_SNAPSHOT_FIELDS = "31,84,86,87,88,7762"
FUNDAMENTAL_SNAPSHOT_FIELDS = "7282,7289,7290"
SCANNER_INSTRUMENT = "STK"
SCANNER_LOCATION = "STK.US.MAJOR"
NEWS_REQUEST_TIMEOUT_SECONDS = 10
NEWS_USER_AGENT = "Mozilla/5.0"
NEWS_MAX_RESPONSE_BYTES = 1_000_000
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
MIN_ORDER_QUANTITY = 0
SUPPORTED_ORDER_TYPES = {"MKT", "LMT", "STP", "STP_LMT"}
SUPPORTED_ORDER_ACTIONS = {"BUY", "SELL"}
ORDER_TYPE_TO_REST = {
    "MKT": "MKT",
    "LMT": "LMT",
    "STP": "STP",
    "STP_LMT": "STOP_LIMIT",
}
ORDER_TYPE_FROM_REST = {
    "STOP_LIMIT": "STP_LMT",
    "STP LMT": "STP_LMT",
}


def log_warning(context: str, error: Exception) -> None:
    print(f"{context} 发生错误: {error}", file=sys.stderr)


@dataclass(frozen=True)
class Position:
    symbol: str
    conid: int
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    pnl_percent: float


@dataclass(frozen=True)
class Quote:
    conid: int
    symbol: str
    last_price: float
    bid: float
    ask: float
    volume: int
    change: float
    change_pct: float


@dataclass(frozen=True)
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
    fills: list[FillSnapshot]


class IBKRRESTTradingClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        default_account_id: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        verify_ssl: bool = DEFAULT_VERIFY_SSL,
        session_factory: Callable[[], requests.Session] = requests.Session,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_account_id = default_account_id
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl
        self.session = session_factory()
        self._authenticated = False

    @staticmethod
    def _parse_numeric(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return None

    @staticmethod
    def _extract_account_id(account: Any) -> Optional[str]:
        if isinstance(account, str):
            return account
        if isinstance(account, dict):
            for key in ("id", "accountId", "account"):
                account_id = account.get(key)
                if account_id:
                    return str(account_id)
        return None

    @classmethod
    def _normalize_account_entry(cls, account: Any) -> Optional[Dict[str, Any]]:
        if isinstance(account, str):
            return {"id": account}
        if isinstance(account, dict):
            account_id = cls._extract_account_id(account)
            if account_id is None:
                return None
            normalized = dict(account)
            normalized.setdefault("id", account_id)
            return normalized
        return None

    def _require_account_id(self, account_id: Optional[str] = None) -> str:
        resolved_account_id = account_id or self.default_account_id
        if not resolved_account_id:
            raise ValueError("account_id is required")
        return resolved_account_id

    def connect(self) -> bool:
        status = self._request_json("GET", "/iserver/auth/status")
        authenticated = bool(status.get("authenticated")) if isinstance(status, dict) else False
        self._authenticated = authenticated
        if not authenticated:
            return False

        self._request_json("POST", "/tickle")
        accounts = self.get_accounts()
        if not self.default_account_id and accounts:
            self.default_account_id = self._extract_account_id(accounts[0])
        return True

    def is_authenticated(self) -> bool:
        return self._authenticated

    def get_accounts(self) -> List[Dict[str, Any]]:
        payload = self._request_json("GET", "/portfolio/accounts")
        raw_accounts: Any = payload
        if isinstance(payload, dict):
            raw_accounts = payload.get("accounts")
        if not isinstance(raw_accounts, list):
            return []

        normalized_accounts: List[Dict[str, Any]] = []
        for account in raw_accounts:
            normalized_entry = self._normalize_account_entry(account)
            if normalized_entry is not None:
                normalized_accounts.append(normalized_entry)
        return normalized_accounts

    def get_balance(self, account_id: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        resolved_account_id = self._require_account_id(account_id)
        summary = self._request_json("GET", f"/portfolio/{resolved_account_id}/summary")
        self._request_json("GET", f"/portfolio/{resolved_account_id}/ledger")
        if not isinstance(summary, list):
            return {}

        result: Dict[str, List[Dict[str, Any]]] = {}
        for row in summary:
            if not isinstance(row, dict):
                continue
            tag = row.get("tag")
            if not tag:
                continue
            entries = result.setdefault(str(tag), [])
            raw_amount = row.get("value", row.get("amount"))
            parsed_amount = self._parse_numeric(raw_amount)
            entries.append(
                {
                    "amount": parsed_amount if parsed_amount is not None else raw_amount,
                    "currency": row.get("currency"),
                    "account": row.get("account", resolved_account_id),
                }
            )
        return result

    def get_positions(self, account_id: Optional[str] = None) -> List[Position]:
        resolved_account_id = self._require_account_id(account_id)
        positions: List[Position] = []
        page_id = 0

        while True:
            page = self._request_json(
                "GET",
                f"/portfolio/{resolved_account_id}/positions/{page_id}",
            )
            if not page:
                break
            if not isinstance(page, list):
                raise RuntimeError("positions payload must be a list")

            for row in page:
                if not isinstance(row, dict):
                    continue
                quantity = float(row.get("position", 0) or 0)
                avg_cost = float(row.get("avgCost", 0) or 0)
                market_value = float(row.get("mktValue", row.get("marketValue", 0)) or 0)
                unrealized_pnl = float(
                    row.get("unrealizedPnl", row.get("unrealizedPNL", 0)) or 0
                )
                cost_basis = avg_cost * quantity
                pnl_percent = (unrealized_pnl / abs(cost_basis) * 100) if cost_basis else 0.0

                positions.append(
                    Position(
                        symbol=str(
                            row.get("contractDesc")
                            or row.get("ticker")
                            or row.get("symbol")
                            or ""
                        ),
                        conid=int(row.get("conid", row.get("conId", 0)) or 0),
                        quantity=quantity,
                        avg_cost=avg_cost,
                        market_value=market_value,
                        unrealized_pnl=unrealized_pnl,
                        pnl_percent=pnl_percent,
                    )
                )
            page_id += 1
        return positions

    def search_symbol(self, symbol: str, **kwargs: Any) -> Optional[dict]:
        params: dict[str, Any] = {"symbol": symbol}
        params.update(kwargs)
        payload = self._request_json("GET", "/iserver/secdef/search", params=params)
        if not isinstance(payload, list) or not payload:
            return None
        first = payload[0]
        return first if isinstance(first, dict) else None

    @classmethod
    def _parse_percent(cls, value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("%"):
            text = text[:-1]
        return cls._parse_numeric(text)

    @classmethod
    def _extract_conid(cls, contract: Any) -> Optional[int]:
        if not isinstance(contract, dict):
            return None
        conid = cls._parse_numeric(contract.get("conid", contract.get("conId")))
        if conid is None or conid <= 0:
            return None
        return int(conid)

    @staticmethod
    def _normalize_optional_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _parse_int(cls, value: Any) -> Optional[int]:
        parsed = cls._parse_numeric(value)
        if parsed is None:
            return None
        return int(parsed)

    @staticmethod
    def _payload_list(payload: Any) -> List[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("orders", "trades", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            return [payload]
        return []

    @classmethod
    def _extract_reply_id(cls, payload: Any) -> Optional[str]:
        for item in cls._payload_list(payload):
            reply_id = item.get("id", item.get("replyId", item.get("reply_id")))
            normalized = cls._normalize_optional_text(reply_id)
            if normalized is not None:
                return normalized
        return None

    def _resolve_conid(self, spec: ContractSpec) -> int:
        if spec.con_id is not None:
            conid = self._parse_int(spec.con_id)
            if conid is None or conid <= 0:
                raise ValueError("contract con_id must be a positive integer")
            return conid

        contract = self.search_symbol(spec.symbol)
        conid = self._extract_conid(contract)
        if conid is None:
            raise ValueError(f"unable to resolve conid for symbol: {spec.symbol}")
        return conid

    def _build_order_payload(self, request: OrderRequest) -> Dict[str, Any]:
        order_type = request.order_type.upper()
        if order_type not in SUPPORTED_ORDER_TYPES:
            raise ValueError(f"unsupported order_type: {request.order_type}")

        action = request.action.upper()
        if action not in SUPPORTED_ORDER_ACTIONS:
            raise ValueError(f"unsupported action: {request.action}")

        if request.quantity <= MIN_ORDER_QUANTITY:
            raise ValueError("quantity must be greater than zero")

        if order_type == "LMT" and request.limit_price is None:
            raise ValueError("limit_price required for LMT order")
        if order_type == "STP" and request.stop_price is None:
            raise ValueError("stop_price required for STP order")
        if order_type == "STP_LMT" and (request.limit_price is None or request.stop_price is None):
            raise ValueError("stop_price and limit_price required for STP_LMT order")

        payload: Dict[str, Any] = {
            "conid": self._resolve_conid(request.contract),
            "side": action,
            "quantity": request.quantity,
            "orderType": ORDER_TYPE_TO_REST[order_type],
        }
        if request.limit_price is not None:
            payload["price"] = request.limit_price
        if request.stop_price is not None:
            payload["auxPrice"] = request.stop_price
        if request.tif is not None:
            payload["tif"] = request.tif
        if request.outside_rth is not None:
            payload["outsideRTH"] = request.outside_rth
        if request.account is not None:
            payload["account"] = request.account
        if request.transmit is not None:
            payload["transmit"] = request.transmit
        return payload

    def _order_snapshot_from_rest(self, item: Dict[str, Any]) -> OrderSnapshot:
        order_type_raw = self._normalize_optional_text(
            item.get("orderType", item.get("order_type"))
        )
        order_type = ORDER_TYPE_FROM_REST.get(order_type_raw or "", order_type_raw)
        return OrderSnapshot(
            order_id=self._parse_int(item.get("orderId", item.get("order_id", item.get("id")))),
            perm_id=self._parse_int(item.get("permId", item.get("perm_id"))),
            symbol=self._normalize_optional_text(
                item.get("ticker", item.get("symbol", item.get("localSymbol")))
            ),
            sec_type=self._normalize_optional_text(item.get("secType", item.get("sec_type"))),
            action=self._normalize_optional_text(item.get("side", item.get("action"))),
            order_type=order_type,
            total_quantity=self._parse_numeric(
                item.get("totalSize", item.get("totalQuantity", item.get("quantity")))
            ),
            limit_price=self._parse_numeric(
                item.get("price", item.get("lmtPrice", item.get("limit_price")))
            ),
            stop_price=self._parse_numeric(
                item.get("auxPrice", item.get("stopPrice", item.get("stop_price")))
            ),
            status=self._normalize_optional_text(item.get("status", item.get("orderStatus"))),
            filled=self._parse_numeric(
                item.get("filledQuantity", item.get("filled", item.get("filled_qty")))
            ),
            remaining=self._parse_numeric(
                item.get("remainingQuantity", item.get("remaining"))
            ),
            avg_fill_price=self._parse_numeric(
                item.get("avgPrice", item.get("avgFillPrice"))
            ),
            last_fill_price=self._parse_numeric(
                item.get("lastExecutionPrice", item.get("lastFillPrice"))
            ),
            exchange=self._normalize_optional_text(
                item.get("listingExchange", item.get("exchange"))
            ),
            account=self._normalize_optional_text(
                item.get("acct", item.get("account", item.get("accountId")))
            ),
            time=self._normalize_optional_text(
                item.get("lastExecutionTime", item.get("time"))
            ),
        )

    def _fill_snapshot_from_rest(self, item: Dict[str, Any]) -> FillSnapshot:
        return FillSnapshot(
            execution_id=self._normalize_optional_text(
                item.get("execId", item.get("execution_id", item.get("executionId")))
            ),
            time=self._normalize_optional_text(item.get("time")),
            price=self._parse_numeric(item.get("price")),
            quantity=self._parse_numeric(item.get("shares", item.get("quantity"))),
            exchange=self._normalize_optional_text(item.get("exchange")),
        )

    def _trade_snapshot_from_rest(self, item: Dict[str, Any]) -> TradeSnapshot:
        execution_rows = item.get("execution", item.get("executions", item.get("fills", [])))
        if isinstance(execution_rows, dict):
            execution_rows = [execution_rows]
        fills = [
            self._fill_snapshot_from_rest(fill)
            for fill in execution_rows
            if isinstance(fill, dict)
        ]
        return TradeSnapshot(order=self._order_snapshot_from_rest(item), fills=fills)

    @classmethod
    def _first_payload_item(cls, payload: Any) -> Dict[str, Any]:
        rows = cls._payload_list(payload)
        if not rows:
            raise RuntimeError("empty response payload")
        return rows[0]

    def get_quote(self, symbol: str) -> Optional[Quote]:
        contract = self.search_symbol(symbol)
        if contract is None:
            return None

        conid = self._extract_conid(contract)
        if conid is None:
            return None
        payload = self._request_json(
            "GET",
            "/iserver/marketdata/snapshot",
            params={"conids": conid, "fields": QUOTE_SNAPSHOT_FIELDS},
        )
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        if not isinstance(row, dict):
            return None

        last_price = self._parse_numeric(row.get("31")) or 0.0
        close_price = self._parse_numeric(row.get("88")) or 0.0
        bid = self._parse_numeric(row.get("84")) or 0.0
        ask = self._parse_numeric(row.get("86")) or 0.0
        volume = int(self._parse_numeric(row.get("87")) or 0)
        change = round(last_price - close_price, 2) if close_price else 0.0
        parsed_change_pct = self._parse_percent(row.get("7762"))
        change_pct = (
            round(parsed_change_pct, 2)
            if parsed_change_pct is not None
            else (round(change / close_price * 100, 2) if close_price else 0.0)
        )

        return Quote(
            conid=conid,
            symbol=str(contract.get("symbol") or symbol),
            last_price=last_price,
            bid=bid,
            ask=ask,
            volume=volume,
            change=change,
            change_pct=change_pct,
        )

    def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]:
        contract = self.search_symbol(symbol)
        if contract is None:
            return None

        conid = self._extract_conid(contract)
        if conid is None:
            return None

        details: dict[str, Any] = {}
        market_row: dict[str, Any] = {}

        try:
            info = self._request_json("GET", f"/iserver/contract/{conid}/info")
            if isinstance(info, dict):
                details = info
        except Exception as err:
            log_warning(f"get_fundamentals({symbol}) info", err)

        try:
            snapshot = self._request_json(
                "GET",
                "/iserver/marketdata/snapshot",
                params={"conids": conid, "fields": FUNDAMENTAL_SNAPSHOT_FIELDS},
            )
            market = snapshot[0] if isinstance(snapshot, list) and snapshot else {}
            if isinstance(market, dict):
                market_row = market
        except Exception as err:
            log_warning(f"get_fundamentals({symbol}) snapshot", err)

        return FundamentalData(
            conid=conid,
            symbol=symbol,
            company_name=str(details.get("companyName") or contract.get("companyName") or symbol),
            industry=str(details.get("industry") or ""),
            category=str(details.get("sectorGroup") or details.get("category") or ""),
            market_cap="N/A",
            pe_ratio="N/A",
            eps="N/A",
            dividend_yield="N/A",
            high_52w=str(market_row.get("7289") or "N/A"),
            low_52w=str(market_row.get("7290") or "N/A"),
            avg_volume=str(market_row.get("7282") or "N/A"),
        )

    def run_scanner(self, scan_type: str = "TOP_PERC_GAIN", size: int = 10) -> List[dict]:
        payload = {
            "instrument": SCANNER_INSTRUMENT,
            "type": scan_type,
            "location": SCANNER_LOCATION,
            "size": str(size),
        }
        rows = self._request_json("POST", "/iserver/scanner/run", payload=payload)
        if not isinstance(rows, list):
            return []
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
            if isinstance(row, dict)
        ]

    def get_company_news(self, symbol: str, limit: int = 5) -> List[dict]:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        headers = {"User-Agent": NEWS_USER_AGENT}
        try:
            response = requests.get(url, headers=headers, timeout=NEWS_REQUEST_TIMEOUT_SECONDS)
        except Exception as err:
            log_warning(f"get_company_news({symbol})", err)
            return []

        if response.status_code != 200:
            log_warning(
                f"get_company_news({symbol})",
                RuntimeError(f"unexpected status {response.status_code}"),
            )
            return []

        raw_content = getattr(response, "content", None)
        if raw_content is None:
            raw_content = (response.text or "").encode("utf-8")
        if len(raw_content) > NEWS_MAX_RESPONSE_BYTES:
            log_warning(
                f"get_company_news({symbol})",
                RuntimeError("response too large"),
            )
            return []

        try:
            root = ET.fromstring(response.text)
        except Exception as err:
            log_warning(f"get_company_news({symbol})", err)
            return []

        news: List[dict] = []
        for item in root.findall(".//item")[:limit]:
            title = item.find("title").text if item.find("title") is not None else ""
            pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
            link = item.find("link").text if item.find("link") is not None else ""
            news.append({"title": title, "date": pub_date, "link": link})
        return news

    def place_order(self, request: OrderRequest) -> TradeSnapshot:
        account_id = self._require_account_id(request.account)
        payload = {"orders": [self._build_order_payload(request)]}
        result = self._request_json(
            "POST",
            f"/iserver/account/{account_id}/orders",
            payload=payload,
        )
        reply_id = self._extract_reply_id(result)
        if reply_id is not None:
            result = self._request_json(
                "POST",
                f"/iserver/reply/{reply_id}",
                payload={"confirmed": True},
            )
        return self._trade_snapshot_from_rest(self._first_payload_item(result))

    def cancel_order(self, order_id: int, account_id: Optional[str] = None) -> Dict[str, Any]:
        resolved_account_id = self._require_account_id(account_id)
        payload = self._request_json(
            "DELETE",
            f"/iserver/account/{resolved_account_id}/order/{order_id}",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("cancel_order response must be an object")
        return payload

    def modify_order(
        self,
        request: ModifyOrderRequest,
        account_id: Optional[str] = None,
    ) -> TradeSnapshot:
        resolved_account_id = self._require_account_id(account_id)
        payload: Dict[str, Any] = {}
        if request.quantity is not None:
            payload["quantity"] = request.quantity
        if request.limit_price is not None and request.stop_price is not None:
            payload["orderType"] = ORDER_TYPE_TO_REST["STP_LMT"]
        elif request.limit_price is not None:
            payload["orderType"] = ORDER_TYPE_TO_REST["LMT"]
        elif request.stop_price is not None:
            payload["orderType"] = ORDER_TYPE_TO_REST["STP"]
        if request.limit_price is not None:
            payload["price"] = request.limit_price
        if request.stop_price is not None:
            payload["auxPrice"] = request.stop_price
        if request.tif is not None:
            payload["tif"] = request.tif
        if request.outside_rth is not None:
            payload["outsideRTH"] = request.outside_rth
        if request.transmit is not None:
            payload["transmit"] = request.transmit

        result = self._request_json(
            "POST",
            f"/iserver/account/{resolved_account_id}/order/{request.order_id}",
            payload=payload,
        )
        return self._trade_snapshot_from_rest(self._first_payload_item(result))

    def get_open_orders(self) -> List[OrderSnapshot]:
        payload = self._request_json("GET", "/iserver/account/orders")
        return [self._order_snapshot_from_rest(item) for item in self._payload_list(payload)]

    def get_orders(self) -> List[OrderSnapshot]:
        payload = self._request_json("GET", "/iserver/account/orders")
        return [self._order_snapshot_from_rest(item) for item in self._payload_list(payload)]

    def get_trades(self) -> List[TradeSnapshot]:
        payload = self._request_json("GET", "/iserver/account/trades")
        return [self._trade_snapshot_from_rest(item) for item in self._payload_list(payload)]

    def get_fills(self) -> List[FillSnapshot]:
        fills: List[FillSnapshot] = []
        for trade in self.get_trades():
            fills.extend(trade.fills)
        return fills

    def get_historical_data(
        self,
        symbol: str,
        duration: str = "3 M",
        bar_size: str = "1 day",
    ) -> List[dict]:
        contract = self.search_symbol(symbol)
        if contract is None:
            return []
        conid = self._extract_conid(contract)
        if conid is None:
            return []

        period = HISTORY_PERIOD_MAP.get(duration, duration)
        bar = HISTORY_BAR_MAP.get(bar_size, bar_size)
        payload = self._request_json(
            "GET",
            "/iserver/marketdata/history",
            params={"conid": conid, "period": period, "bar": bar},
        )
        bars = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(bars, list):
            return []
        return [
            {
                "date": row.get("t"),
                "open": row.get("o"),
                "high": row.get("h"),
                "low": row.get("l"),
                "close": row.get("c"),
                "volume": row.get("v"),
            }
            for row in bars
            if isinstance(row, dict)
        ]

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> Any:
        normalized_method = method.upper()
        response = self.session.request(
            normalized_method,
            f"{self.base_url}{path}",
            params=params,
            json=payload,
            timeout=self.timeout_seconds,
            verify=self.verify_ssl,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"{normalized_method} {path} failed: {response.status_code} {response.text}"
            )
        return response.json()

    def disconnect(self) -> None:
        self.session.close()

#!/usr/bin/env python3
"""IBKR REST Trading Client."""

from __future__ import annotations

import os
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

    def get_quote(self, symbol: str) -> Optional[Quote]:
        contract = self.search_symbol(symbol)
        if contract is None:
            return None

        conid = int(contract.get("conid", contract.get("conId", 0)) or 0)
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
            symbol=symbol,
            last_price=last_price,
            bid=bid,
            ask=ask,
            volume=volume,
            change=change,
            change_pct=change_pct,
        )

    def get_historical_data(
        self,
        symbol: str,
        duration: str = "3 M",
        bar_size: str = "1 day",
    ) -> List[dict]:
        contract = self.search_symbol(symbol)
        if contract is None:
            return []

        period = HISTORY_PERIOD_MAP.get(duration, duration)
        bar = HISTORY_BAR_MAP.get(bar_size, bar_size)
        conid = int(contract.get("conid", contract.get("conId", 0)) or 0)
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

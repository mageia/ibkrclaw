#!/usr/bin/env python3
"""IBKR REST Trading Client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

import requests


DEFAULT_BASE_URL = os.getenv("IBKR_REST_BASE_URL", "https://localhost:5000/v1/api")
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("IBKR_REST_TIMEOUT_SECONDS", "10"))
DEFAULT_VERIFY_SSL = os.getenv("IBKR_REST_VERIFY_SSL", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


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
    fills: list[FillSnapshot]


class IBKRRESTTradingClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        default_account_id: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        verify_ssl: bool = DEFAULT_VERIFY_SSL,
        session_factory: Callable[[], requests.Session] = requests.Session,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_account_id = default_account_id
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl
        self.session = session_factory()

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> Any:
        normalized_method = method.upper()
        response = self.session.request(
            normalized_method,
            f"{self.base_url}{path}",
            params=params,
            json=json_body,
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

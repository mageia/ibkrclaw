#!/usr/bin/env python3
"""
IBKR Trading Client - ib_insync 版本
基础交易连接能力与账户信息读取。
"""

import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable

from ib_insync import *

# Configuration
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4001"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))
MARKET_DATA_TYPE_DELAYED = 3
RECONNECT_BASE_DELAY_SECONDS = 1
RECONNECT_MAX_ATTEMPTS = 3
DEFAULT_STOCK_EXCHANGE = "SMART"
DEFAULT_STOCK_CURRENCY = "USD"


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


def parse_account_summary_value(value: Any) -> Optional[float]:
    """尝试将余额项的 value 字符串转换为浮点数"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace(",", "")
    try:
        return float(normalized)
    except ValueError:
        return None


def get_primary_balance_amount(balance: Dict[str, List[Dict[str, Any]]], tag: str) -> float:
    """返回某个 tag 下第一个可解析为数字的余额"""
    entries = balance.get(tag, [])
    for entry in entries:
        amount = parse_account_summary_value(entry.get("amount"))
        if amount is not None:
            return amount
    return 0.0


def log_warning(context: str, error: Exception) -> None:
    """统一将 warning 输出到 stderr"""
    print(f"{context} 发生错误: {error}", file=sys.stderr)


def build_stock_contract(
    symbol: str,
    *,
    exchange: str = DEFAULT_STOCK_EXCHANGE,
    currency: str = DEFAULT_STOCK_CURRENCY,
    primary_exchange: Optional[str] = None,
) -> Contract:
    """构造标准股票合约，支持可选的 primary exchange"""
    contract = Stock(symbol, exchange, currency)
    if primary_exchange:
        normalized_primary = primary_exchange.strip()
        if normalized_primary:
            contract.primaryExchange = normalized_primary
    return contract


class IBKRTradingClient:
    """
    IBKR 交易客户端 - ib_insync 版
    只包含连接/断线重连/余额/股票查询等基础能力。
    """

    def __init__(
        self,
        host: str = IB_HOST,
        port: int = IB_PORT,
        client_id: int = IB_CLIENT_ID,
        *,
        ib_factory: Callable[[], Any] = IB,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = ib_factory()
        self._setup_reconnect()

    def _setup_reconnect(self) -> None:
        def on_disconnect():
            print(f"[{time.strftime('%H:%M:%S')}] 开始自动重连")
            if self._reconnect_with_backoff():
                print(f"[{time.strftime('%H:%M:%S')}] 重连成功")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] 已达到最大重试次数，停止自动重连")

        self.ib.disconnectedEvent += on_disconnect

    def _reconnect_with_backoff(self) -> bool:
        for attempt in range(1, RECONNECT_MAX_ATTEMPTS + 1):
            delay = RECONNECT_BASE_DELAY_SECONDS * attempt
            time.sleep(delay)
            try:
                self._connect_gateway()
                return True
            except Exception as exc:
                print(f"[{time.strftime('%H:%M:%S')}] 第 {attempt} 次重连失败: {exc}")
        return False

    def _apply_market_data_type(self) -> None:
        self.ib.reqMarketDataType(MARKET_DATA_TYPE_DELAYED)

    def _connect_gateway(self) -> None:
        self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=False)
        self._apply_market_data_type()

    def connect(self) -> bool:
        """连接 IB Gateway"""
        try:
            self._connect_gateway()
            return True
        except Exception as exc:
            print(f"❌ 连接失败: {exc}")
            return False

    def disconnect(self) -> None:
        """断开连接"""
        if self.ib.isConnected():
            self.ib.disconnectedEvent.clear()
            self.ib.disconnect()

    def get_balance(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取账户余额/总结"""
        summary = self.ib.accountSummary()
        result: Dict[str, List[Dict[str, Any]]] = {}
        for item in summary:
            entries = result.setdefault(item.tag, [])
            parsed_amount = parse_account_summary_value(item.value)
            entries.append({
                "amount": parsed_amount if parsed_amount is not None else item.value,
                "currency": item.currency,
                "account": getattr(item, "account", None),
            })
        return result

    def search_symbol(
        self,
        symbol: str,
        *,
        exchange: str = DEFAULT_STOCK_EXCHANGE,
        currency: str = DEFAULT_STOCK_CURRENCY,
        primary_exchange: Optional[str] = None,
    ) -> Optional[Contract]:
        """搜索股票代码，返回 qualified Contract"""
        contract = build_stock_contract(
            symbol,
            exchange=exchange,
            currency=currency,
            primary_exchange=primary_exchange,
        )
        try:
            qualified = self.ib.qualifyContracts(contract)
            if qualified:
                return qualified[0]
        except Exception as err:
            log_warning(f"search_symbol({symbol})", err)
        return None

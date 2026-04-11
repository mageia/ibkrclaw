#!/usr/bin/env python3
"""
IBKR Trading Client - ib_insync 版本
提供交易连接能力，并包含持仓/行情/基本面/历史数据等只读查询能力。
"""

import math
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Callable

from ib_insync import IB, Stock, Contract, ScannerSubscription, TagValue, Option, Future, Order

# Configuration
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4001"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))
MARKET_DATA_TYPE_DELAYED = 3
RECONNECT_BASE_DELAY_SECONDS = 1
RECONNECT_MAX_ATTEMPTS = 3
DEFAULT_STOCK_EXCHANGE = "SMART"
DEFAULT_STOCK_CURRENCY = "USD"
SCANNER_MARKET_CAP_ABOVE = "100000000"
NEWS_REQUEST_TIMEOUT_SECONDS = 10
NEWS_USER_AGENT = "Mozilla/5.0"
NEWS_MAX_RESPONSE_BYTES = 1_000_000
MIN_ORDER_QUANTITY = 0
SUPPORTED_ORDER_TYPES = {"MKT", "LMT", "STP", "STP_LMT"}
SUPPORTED_ORDER_ACTIONS = {"BUY", "SELL"}


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


def _ensure_required(value: Any, field_name: str, sec_type: str) -> Any:
    if value in (None, ""):
        raise ValueError(f"{sec_type} missing required field: {field_name}")
    return value


def build_contract(spec: ContractSpec) -> Contract:
    sec_type = spec.sec_type.upper()
    symbol = _ensure_required(spec.symbol, "symbol", sec_type)

    if sec_type == "STK":
        exchange = spec.exchange or DEFAULT_STOCK_EXCHANGE
        currency = spec.currency or DEFAULT_STOCK_CURRENCY
        contract: Contract = Stock(symbol, exchange, currency)
        contract.secType = "STK"
    elif sec_type == "OPT":
        exchange = _ensure_required(spec.exchange, "exchange", sec_type)
        currency = _ensure_required(spec.currency, "currency", sec_type)
        expiry = _ensure_required(
            spec.last_trade_date_or_contract_month,
            "last_trade_date_or_contract_month",
            sec_type,
        )
        strike = _ensure_required(spec.strike, "strike", sec_type)
        right = _ensure_required(spec.right, "right", sec_type)
        contract = Option(
            symbol,
            expiry,
            strike,
            right,
            exchange,
            currency,
        )
    elif sec_type == "FUT":
        exchange = _ensure_required(spec.exchange, "exchange", sec_type)
        currency = _ensure_required(spec.currency, "currency", sec_type)
        contract_month = _ensure_required(
            spec.last_trade_date_or_contract_month,
            "last_trade_date_or_contract_month",
            sec_type,
        )
        contract = Future(
            symbol,
            contract_month,
            exchange,
            currency,
        )
    else:
        raise ValueError(f"unsupported sec_type: {sec_type}")

    if spec.primary_exchange:
        contract.primaryExchange = spec.primary_exchange
    if spec.local_symbol:
        contract.localSymbol = spec.local_symbol
    if spec.multiplier:
        contract.multiplier = spec.multiplier
    if spec.trading_class:
        contract.tradingClass = spec.trading_class
    if spec.con_id is not None:
        contract.conId = spec.con_id

    return contract


def qualify_contract(ib: IB, spec: ContractSpec) -> Contract:
    contract = build_contract(spec)
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise ValueError(f"qualify_contract returned empty result for {spec}")
    return qualified[0]


def build_order(request: OrderRequest) -> Order:
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
    if order_type == "STP_LMT" and (request.stop_price is None or request.limit_price is None):
        raise ValueError("stop_price and limit_price required for STP_LMT order")

    normalized_type = "STP LMT" if order_type == "STP_LMT" else order_type
    order = Order()
    order.action = action
    order.totalQuantity = request.quantity
    order.orderType = normalized_type

    if order_type in {"LMT", "STP_LMT"}:
        order.lmtPrice = request.limit_price
    if order_type in {"STP", "STP_LMT"}:
        order.auxPrice = request.stop_price

    if request.tif is not None:
        order.tif = request.tif
    if request.outside_rth is not None:
        order.outsideRth = request.outside_rth
    if request.account is not None:
        order.account = request.account
    if request.transmit is not None:
        order.transmit = request.transmit

    return order


def _normalize_order_type(order_type: Optional[str]) -> str:
    if not order_type:
        return ""
    normalized = order_type.strip().upper()
    if normalized == "STP LMT":
        return "STP_LMT"
    return normalized


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _order_snapshot_from_trade(trade: Any) -> OrderSnapshot:
    contract = getattr(trade, "contract", None)
    order = getattr(trade, "order", None)
    order_status = getattr(trade, "orderStatus", None)

    symbol = None
    sec_type = None
    exchange = None
    if contract is not None:
        symbol = _normalize_optional_text(
            getattr(contract, "localSymbol", None) or getattr(contract, "symbol", None)
        )
        sec_type = _normalize_optional_text(getattr(contract, "secType", None))
        exchange = _normalize_optional_text(getattr(contract, "exchange", None))

    order_id = getattr(order, "orderId", None)
    perm_id = getattr(order, "permId", None)
    action = _normalize_optional_text(getattr(order, "action", None) if order is not None else None)
    total_quantity = getattr(order, "totalQuantity", None) if order is not None else None
    order_type = _normalize_order_type(
        getattr(order, "orderType", None) if order is not None else None
    )
    order_type = _normalize_optional_text(order_type)
    limit_price = getattr(order, "lmtPrice", None) if order is not None else None
    stop_price = getattr(order, "auxPrice", None) if order is not None else None
    account = _normalize_optional_text(
        getattr(order, "account", None) if order is not None else None
    )

    status = _normalize_optional_text(
        getattr(order_status, "status", None) if order_status is not None else None
    )
    filled = getattr(order_status, "filled", None) if order_status is not None else None
    remaining = getattr(order_status, "remaining", None) if order_status is not None else None
    avg_fill_price = (
        getattr(order_status, "avgFillPrice", None) if order_status is not None else None
    )
    last_fill_price = (
        getattr(order_status, "lastFillPrice", None) if order_status is not None else None
    )
    time_value = _normalize_optional_text(
        getattr(order_status, "time", None) if order_status is not None else None
    )

    return OrderSnapshot(
        order_id=order_id,
        perm_id=perm_id,
        symbol=symbol,
        sec_type=sec_type,
        action=action,
        order_type=order_type,
        total_quantity=total_quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        status=status,
        filled=filled,
        remaining=remaining,
        avg_fill_price=avg_fill_price,
        last_fill_price=last_fill_price,
        exchange=exchange,
        account=account,
        time=time_value,
    )


def _order_snapshot_from_order(order: Any) -> OrderSnapshot:
    contract = getattr(order, "contract", None)

    symbol = None
    sec_type = None
    exchange = None
    if contract is not None:
        symbol = _normalize_optional_text(
            getattr(contract, "localSymbol", None) or getattr(contract, "symbol", None)
        )
        sec_type = _normalize_optional_text(getattr(contract, "secType", None))
        exchange = _normalize_optional_text(getattr(contract, "exchange", None))
    else:
        symbol = _normalize_optional_text(getattr(order, "symbol", None))
        sec_type = _normalize_optional_text(getattr(order, "secType", None))
        exchange = _normalize_optional_text(getattr(order, "exchange", None))

    order_id = getattr(order, "orderId", None)
    perm_id = getattr(order, "permId", None)
    action = _normalize_optional_text(getattr(order, "action", None))
    total_quantity = getattr(order, "totalQuantity", None)
    order_type = _normalize_order_type(getattr(order, "orderType", None))
    order_type = _normalize_optional_text(order_type)
    limit_price = getattr(order, "lmtPrice", None)
    stop_price = getattr(order, "auxPrice", None)
    account = _normalize_optional_text(getattr(order, "account", None))

    order_status = getattr(order, "orderStatus", None)
    status = _normalize_optional_text(
        getattr(order_status, "status", None) if order_status is not None else getattr(order, "status", None)
    )
    filled = (
        getattr(order_status, "filled", None) if order_status is not None else getattr(order, "filled", None)
    )
    remaining = (
        getattr(order_status, "remaining", None) if order_status is not None else getattr(order, "remaining", None)
    )
    avg_fill_price = (
        getattr(order_status, "avgFillPrice", None)
        if order_status is not None
        else getattr(order, "avgFillPrice", None)
    )
    last_fill_price = (
        getattr(order_status, "lastFillPrice", None)
        if order_status is not None
        else getattr(order, "lastFillPrice", None)
    )
    time_value = _normalize_optional_text(
        getattr(order_status, "time", None) if order_status is not None else getattr(order, "time", None)
    )

    return OrderSnapshot(
        order_id=order_id,
        perm_id=perm_id,
        symbol=symbol,
        sec_type=sec_type,
        action=action,
        order_type=order_type,
        total_quantity=total_quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        status=status,
        filled=filled,
        remaining=remaining,
        avg_fill_price=avg_fill_price,
        last_fill_price=last_fill_price,
        exchange=exchange,
        account=account,
        time=time_value,
    )


def _order_snapshot_from_item(item: Any) -> OrderSnapshot:
    if getattr(item, "order", None) is not None:
        return _order_snapshot_from_trade(item)
    return _order_snapshot_from_order(item)


def _fill_snapshot_from_fill(fill: Any) -> FillSnapshot:
    execution = getattr(fill, "execution", None)
    execution_id = _normalize_optional_text(
        getattr(execution, "execId", None) if execution is not None else None
    )
    time_value = _normalize_optional_text(
        getattr(execution, "time", None) if execution is not None else None
    )
    price = getattr(execution, "price", None) if execution is not None else None
    quantity = getattr(execution, "shares", None) if execution is not None else None
    exchange = _normalize_optional_text(
        getattr(execution, "exchange", None) if execution is not None else None
    )

    return FillSnapshot(
        execution_id=execution_id,
        time=time_value,
        price=price,
        quantity=quantity,
        exchange=exchange,
    )


def _trade_snapshot_from_trade(trade: Any) -> TradeSnapshot:
    fills = getattr(trade, "fills", None) or []
    return TradeSnapshot(
        order=_order_snapshot_from_trade(trade),
        fills=[_fill_snapshot_from_fill(fill) for fill in fills],
    )


def _trade_like_from_item(item: Any) -> Any:
    order = getattr(item, "order", None) or item
    contract = getattr(item, "contract", None)
    order_status = getattr(item, "orderStatus", None)
    fills = getattr(item, "fills", None) or []
    return SimpleNamespace(order=order, contract=contract, orderStatus=order_status, fills=fills)


def _order_id_from_item(item: Any) -> Optional[int]:
    order = getattr(item, "order", None)
    if order is not None:
        return getattr(order, "orderId", None)
    return getattr(item, "orderId", None)


def _clone_order(order: Any) -> Order:
    if order is None:
        raise ValueError("order is required")
    cloned = Order()
    for key, value in getattr(order, "__dict__", {}).items():
        setattr(cloned, key, value)
    return cloned


def _qualify_existing_contract(ib: IB, contract: Contract) -> Contract:
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise ValueError("qualifyContracts returned empty result for contract")
    return qualified[0]


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


def _safe_market_value(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isnan(numeric):
            return default
        return numeric
    return default


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
    支持连接/断线重连/余额/股票查询，并提供只读查询能力。
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

        self._disconnect_handler = on_disconnect
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
            self._remove_disconnect_handler()
            self.ib.disconnect()

    def _remove_disconnect_handler(self) -> None:
        handler = getattr(self, "_disconnect_handler", None)
        if handler is None:
            return
        event = self.ib.disconnectedEvent
        if hasattr(event, "__isub__"):
            try:
                event -= handler
            except ValueError:
                return
            return
        disconnect = getattr(event, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect(handler)
            except ValueError:
                return
            return
        remove = getattr(event, "remove", None)
        if callable(remove):
            try:
                remove(handler)
            except ValueError:
                return

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

    def get_positions(self) -> List[Position]:
        """获取当前持仓（使用 portfolio() 获取服务端计算的市值和盈亏，无需行情订阅）"""
        portfolio_items = self.ib.portfolio()
        positions: List[Position] = []
        for item in portfolio_items:
            contract = item.contract
            quantity = _safe_market_value(item.position)
            avg_cost = _safe_market_value(item.averageCost)
            market_value = _safe_market_value(item.marketValue)
            unrealized_pnl = _safe_market_value(item.unrealizedPNL)

            cost_basis = avg_cost * quantity if quantity else 0
            pnl_percent = (unrealized_pnl / abs(cost_basis) * 100) if cost_basis else 0

            positions.append(Position(
                symbol=contract.localSymbol or contract.symbol,
                conid=contract.conId,
                quantity=quantity,
                avg_cost=avg_cost,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                pnl_percent=pnl_percent,
            ))
        return positions

    def get_quote(self, symbol: str) -> Optional[Quote]:
        """获取行情快照"""
        contract = self.search_symbol(symbol)
        if not contract:
            return None

        try:
            tickers = self.ib.reqTickers(contract)
            if len(tickers) != 1:
                log_warning(
                    f"get_quote({symbol})",
                    RuntimeError(f"unexpected ticker count {len(tickers)}"),
                )
                return None
            ticker = tickers[0]
            last_price = _safe_market_value(ticker.last)
            close_price = _safe_market_value(ticker.close)
            bid = _safe_market_value(ticker.bid)
            ask = _safe_market_value(ticker.ask)
            volume = int(_safe_market_value(ticker.volume))

            if not last_price:
                last_price = close_price

            change = (last_price - close_price) if last_price and close_price else 0
            change_pct = (change / close_price * 100) if close_price else 0

            return Quote(
                conid=contract.conId,
                symbol=symbol,
                last_price=last_price or 0,
                bid=bid,
                ask=ask,
                volume=volume,
                change=round(change, 2),
                change_pct=round(change_pct, 2),
            )
        except Exception as err:
            log_warning(f"get_quote({symbol})", err)
            return None

    def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]:
        """获取个股基本面指标"""
        contract = self.search_symbol(symbol)
        if not contract:
            return None

        company_name = contract.description if hasattr(contract, "description") else ""
        industry = ""
        category = ""
        market_cap = "N/A"
        pe_ratio = "N/A"
        eps = "N/A"
        dividend_yield = "N/A"
        high_52w = "N/A"
        low_52w = "N/A"
        avg_volume = "N/A"

        try:
            xml_data = self.ib.reqFundamentalData(contract, "ReportSnapshot")
            if xml_data:
                root = ET.fromstring(xml_data)
                name_el = root.find(".//CoGeneralInfo/CoName")
                if name_el is not None:
                    company_name = name_el.text

                ind_el = root.find(".//Industry")
                if ind_el is not None:
                    industry = ind_el.get("type", "")
                    category = ind_el.text or ""

                for ratio in root.findall(".//Ratio"):
                    field_name = ratio.get("FieldName", "")
                    value = ratio.text or "N/A"
                    if field_name == "MKTCAP":
                        market_cap = value
                    elif field_name == "PEEXCLXOR":
                        pe_ratio = value
                    elif field_name == "TTMEPSXCLX":
                        eps = value
                    elif field_name == "YIELD":
                        dividend_yield = value
                    elif field_name == "NHIG":
                        high_52w = value
                    elif field_name == "NLOW":
                        low_52w = value
                    elif field_name in ("APTS10DAVG", "VOL10DAVG"):
                        avg_volume = value
        except Exception as err:
            log_warning(f"get_fundamentals({symbol})", err)

        try:
            [ticker] = self.ib.reqTickers(contract)
            if high_52w == "N/A" and getattr(ticker, "high", None):
                high_52w = str(ticker.high)
            if low_52w == "N/A" and getattr(ticker, "low", None):
                low_52w = str(ticker.low)
        except Exception as err:
            log_warning(f"get_fundamentals({symbol}) ticker fallback", err)

        return FundamentalData(
            conid=contract.conId,
            symbol=symbol,
            company_name=company_name,
            industry=industry,
            category=category,
            market_cap=market_cap,
            pe_ratio=pe_ratio,
            eps=eps,
            dividend_yield=dividend_yield,
            high_52w=high_52w,
            low_52w=low_52w,
            avg_volume=avg_volume,
        )

    def get_historical_data(
        self,
        symbol: str,
        duration: str = "3 M",
        bar_size: str = "1 day",
    ) -> List[dict]:
        """获取历史 K 线数据"""
        contract = self.search_symbol(symbol)
        if not contract:
            return []

        try:
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
            )
            return [
                {
                    "date": str(bar.date),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in bars
            ]
        except Exception as err:
            log_warning(f"get_historical_data({symbol})", err)
            return []

    def run_scanner(self, scan_type: str = "TOP_PERC_GAIN", size: int = 10) -> List[dict]:
        """全市场智能扫描"""
        try:
            subscription = ScannerSubscription(
                instrument="STK",
                locationCode="STK.US.MAJOR",
                scanCode=scan_type,
                numberOfRows=size,
            )
            tag_values = [
                TagValue("marketCapAbove", SCANNER_MARKET_CAP_ABOVE),
            ]
            results = self.ib.reqScannerData(
                subscription,
                scannerSubscriptionFilterOptions=tag_values,
            )
            return [
                {
                    "rank": item.rank,
                    "symbol": item.contractDetails.contract.symbol,
                    "conid": item.contractDetails.contract.conId,
                    "distance": item.distance,
                    "benchmark": item.benchmark,
                    "projection": item.projection,
                }
                for item in results
            ]
        except Exception as err:
            log_warning(f"run_scanner({scan_type})", err)
            return []

    def get_company_news(self, symbol: str, limit: int = 5) -> List[dict]:
        """获取公司最新新闻"""
        import requests

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
            text = response.text or ""
            raw_content = text.encode("utf-8")
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

    def place_order_raw(self, contract: Contract, order: Order) -> Any:
        """原生下单：qualify 现有 contract 并返回原始 Trade"""
        qualified = _qualify_existing_contract(self.ib, contract)
        return self.ib.placeOrder(qualified, order)

    def place_order(self, request: OrderRequest) -> TradeSnapshot:
        """高层下单：按请求构建合约和订单，并返回标准化 TradeSnapshot"""
        contract = qualify_contract(self.ib, request.contract)
        order = build_order(request)
        trade = self.ib.placeOrder(contract, order)
        return _trade_snapshot_from_trade(trade)

    def _find_trade_by_order_id(self, order_id: int) -> Any:
        for trade in self.get_trades_raw():
            if _order_id_from_item(trade) == order_id:
                return trade
        for order in self.get_open_orders_raw():
            if _order_id_from_item(order) == order_id:
                return order
        for order in self.get_orders_raw():
            if _order_id_from_item(order) == order_id:
                return order
        raise ValueError(f"order_id {order_id} not found")

    def cancel_order_raw(self, order_or_trade: Any) -> Any:
        order = getattr(order_or_trade, "order", None) or order_or_trade
        if order is None:
            raise ValueError("order is required for cancel_order_raw")
        self.ib.cancelOrder(order)
        return order_or_trade

    def cancel_order(self, order_id: int) -> TradeSnapshot:
        order_item = self._find_trade_by_order_id(order_id)
        self.cancel_order_raw(order_item)
        return _trade_snapshot_from_trade(_trade_like_from_item(order_item))

    def modify_order_raw(self, trade: Any, request: ModifyOrderRequest) -> Any:
        order = getattr(trade, "order", None)
        if order is None:
            raise ValueError("trade order is required for modify_order_raw")
        contract = getattr(trade, "contract", None)
        if contract is None:
            raise ValueError("trade contract is required for modify_order_raw")
        updated = _clone_order(order)

        if request.quantity is not None:
            updated.totalQuantity = request.quantity
        if request.limit_price is not None:
            updated.lmtPrice = request.limit_price
        if request.stop_price is not None:
            updated.auxPrice = request.stop_price
        if request.tif is not None:
            updated.tif = request.tif
        if request.outside_rth is not None:
            updated.outsideRth = request.outside_rth
        if request.transmit is not None:
            updated.transmit = request.transmit

        return self.ib.placeOrder(contract, updated)

    def modify_order(self, request: ModifyOrderRequest) -> TradeSnapshot:
        order_item = self._find_trade_by_order_id(request.order_id)
        trade = _trade_like_from_item(order_item)
        updated_trade = self.modify_order_raw(trade, request)
        return _trade_snapshot_from_trade(updated_trade)

    def get_open_orders_raw(self) -> List[Any]:
        return self.ib.openOrders()

    def get_orders_raw(self) -> List[Any]:
        return self.ib.orders()

    def get_trades_raw(self) -> List[Any]:
        return self.ib.trades()

    def get_fills_raw(self) -> List[Any]:
        return self.ib.fills()

    def get_open_orders(self) -> List[OrderSnapshot]:
        return [_order_snapshot_from_item(order) for order in self.get_open_orders_raw()]

    def get_orders(self) -> List[OrderSnapshot]:
        return [_order_snapshot_from_item(order) for order in self.get_orders_raw()]

    def get_trades(self) -> List[TradeSnapshot]:
        return [_trade_snapshot_from_trade(trade) for trade in self.get_trades_raw()]

    def get_fills(self) -> List[FillSnapshot]:
        return [_fill_snapshot_from_fill(fill) for fill in self.get_fills_raw()]

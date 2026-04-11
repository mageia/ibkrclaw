import sys
import types

ib_insync_stub = types.ModuleType("ib_insync")

class _DummyIB:
    def __init__(self, *args, **kwargs):
        pass


def _noop(*args, **kwargs):
    pass

ib_insync_stub.IB = _DummyIB
ib_insync_stub.util = types.SimpleNamespace(patchAsyncio=_noop)

class StockProxy:
    def __init__(self, symbol, exchange, currency):
        self.symbol = symbol
        self.exchange = exchange
        self.currency = currency


ib_insync_stub.Stock = StockProxy

class ScannerSubscription:
    def __init__(self, *args, **kwargs):
        pass


ib_insync_stub.ScannerSubscription = ScannerSubscription

class TagValue:
    def __init__(self, tag, value):
        self.tag = tag
        self.value = value


ib_insync_stub.TagValue = TagValue


class Contract:
    def __init__(self, **kwargs):
        for attr, value in kwargs.items():
            setattr(self, attr, value)


class Option(Contract):
    def __init__(self, symbol, lastTradeDateOrContractMonth, strike, right, exchange, currency):
        super().__init__(
            symbol=symbol,
            lastTradeDateOrContractMonth=lastTradeDateOrContractMonth,
            strike=strike,
            right=right,
            exchange=exchange,
            currency=currency,
            secType="OPT",
        )


class Future(Contract):
    def __init__(self, symbol, lastTradeDateOrContractMonth, exchange, currency):
        super().__init__(
            symbol=symbol,
            lastTradeDateOrContractMonth=lastTradeDateOrContractMonth,
            exchange=exchange,
            currency=currency,
            secType="FUT",
        )


class Order:
    def __init__(self, **kwargs):
        for attr, value in kwargs.items():
            setattr(self, attr, value)


class MarketOrder(Order):
    def __init__(self, action, totalQuantity):
        super().__init__(action=action, totalQuantity=totalQuantity, orderType="MKT")


class LimitOrder(Order):
    def __init__(self, action, totalQuantity, lmtPrice):
        super().__init__(
            action=action,
            totalQuantity=totalQuantity,
            lmtPrice=lmtPrice,
            orderType="LMT",
        )


class StopOrder(Order):
    def __init__(self, action, totalQuantity, stopPrice):
        super().__init__(
            action=action,
            totalQuantity=totalQuantity,
            stopPrice=stopPrice,
            orderType="STP",
        )


class StopLimitOrder(Order):
    def __init__(self, action, totalQuantity, stopPrice, lmtPrice):
        super().__init__(
            action=action,
            totalQuantity=totalQuantity,
            stopPrice=stopPrice,
            lmtPrice=lmtPrice,
            orderType="STP LMT",
        )


ib_insync_stub.Contract = Contract
ib_insync_stub.Option = Option
ib_insync_stub.Future = Future
ib_insync_stub.Order = Order
ib_insync_stub.MarketOrder = MarketOrder
ib_insync_stub.LimitOrder = LimitOrder
ib_insync_stub.StopOrder = StopOrder
ib_insync_stub.StopLimitOrder = StopLimitOrder
ib_insync_stub.__all__ = [
    "IB",
    "Stock",
    "ScannerSubscription",
    "TagValue",
    "Contract",
    "Option",
    "Future",
    "Order",
    "MarketOrder",
    "LimitOrder",
    "StopOrder",
    "StopLimitOrder",
    "util",
]

sys.modules.setdefault("ib_insync", ib_insync_stub)

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
ib_insync_stub.__all__ = ["IB", "Stock", "ScannerSubscription", "TagValue", "util"]

sys.modules.setdefault("ib_insync", ib_insync_stub)

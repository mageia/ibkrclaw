# IBKR Read-Only Phase 3 Query and Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展 `search_symbol()` 的可配置查询能力，并让 CLI 正确展示多账户/多币种余额明细，同时保持现有默认行为兼容。

**Architecture:** 保持 `scripts/ibkr_readonly.py` 单文件结构，不引入新模块。通过“测试先行”分别锁定 `search_symbol()` 的新参数行为与余额明细格式化行为，再以最小改动补充 contract 构造 helper 和 CLI 展示 helper，让默认调用仍保持 `SMART/USD` 兼容路径，而新增调用能覆盖 exchange/currency/primary exchange 场景。

**Tech Stack:** Python 3.9+, pytest, ib_insync

---

## File Structure

- `scripts/ibkr_readonly.py`
  - 新增可复用的 contract 构造 helper
  - 扩展 `search_symbol()` 参数
  - 新增余额明细格式化 helper
  - 调整 `main()` 输出
- `tests/test_ibkr_readonly.py`
  - 扩展 FakeIB 的 contract 观察能力
  - 新增 `search_symbol()` 参数化测试
  - 新增余额明细格式化测试

---

### Task 1: 用失败测试锁定 `search_symbol()` 的可配置 contract 构造

**Files:**
- Modify: `tests/test_ibkr_readonly.py`
- Test: `tests/test_ibkr_readonly.py`

- [ ] **Step 1: 扩展 `FakeIB`，让测试能观察传给 `qualifyContracts()` 的 contract**

把 `tests/test_ibkr_readonly.py` 里的 `FakeIB` 补成下面这样：

```python
class FakeIB:
    def __init__(self, connect_outcomes=None):
        self.disconnectedEvent = FakeEvent()
        self.connect_calls = []
        self.market_data_types = []
        self.accountSummary = lambda: []
        self._connect_outcomes = list(connect_outcomes or [])
        self.qualify_contract_inputs = []

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
```

- [ ] **Step 2: 追加两个失败测试，分别锁定默认行为和自定义参数行为**

在 `tests/test_ibkr_readonly.py` 中追加：

```python
def test_search_symbol_keeps_default_smart_usd_contract(monkeypatch):
    client, fake_ib = build_client(monkeypatch)

    contract = client.search_symbol("AAPL")

    assert contract is not None
    observed = fake_ib.qualify_contract_inputs[-1]
    assert observed.symbol == "AAPL"
    assert observed.exchange == "SMART"
    assert observed.currency == "USD"
    assert getattr(observed, "primaryExchange", "") in ("", None)


def test_search_symbol_accepts_exchange_currency_and_primary_exchange(monkeypatch):
    client, fake_ib = build_client(monkeypatch)

    contract = client.search_symbol(
        "700",
        exchange="SEHK",
        currency="HKD",
        primary_exchange="SEHK",
    )

    assert contract is not None
    observed = fake_ib.qualify_contract_inputs[-1]
    assert observed.symbol == "700"
    assert observed.exchange == "SEHK"
    assert observed.currency == "HKD"
    assert observed.primaryExchange == "SEHK"
```

- [ ] **Step 3: 运行红灯测试，确认当前实现还不支持这些参数**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_ibkr_readonly.py::test_search_symbol_keeps_default_smart_usd_contract \
  tests/test_ibkr_readonly.py::test_search_symbol_accepts_exchange_currency_and_primary_exchange -q
```

Expected:

- FAIL
- 失败原因包括：
  - 当前 `search_symbol()` 不接受 `exchange` / `currency` / `primary_exchange`
  - 或 contract 未携带 `primaryExchange`

- [ ] **Step 4: 提交红灯测试**

```bash
git add tests/test_ibkr_readonly.py
git commit -m "test: lock configurable symbol search contracts"
```

---

### Task 2: 实现可配置的 symbol 查询

**Files:**
- Modify: `scripts/ibkr_readonly.py`
- Test: `tests/test_ibkr_readonly.py`

- [ ] **Step 1: 新增 contract 构造 helper，并扩展 `search_symbol()` 签名**

把 `scripts/ibkr_readonly.py` 中 `search_symbol()` 相关逻辑改成下面这样：

```python
def build_stock_contract(
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    primary_exchange: Optional[str] = None,
) -> Contract:
    contract = Stock(symbol, exchange, currency)
    if primary_exchange:
        contract.primaryExchange = primary_exchange
    return contract


class IBKRReadOnlyClient:
    def search_symbol(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
        primary_exchange: Optional[str] = None,
    ) -> Optional[Contract]:
        """搜索股票代码，返回 qualified Contract"""
        contract = build_stock_contract(
            symbol=symbol,
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
```

- [ ] **Step 2: 重新运行刚才的两个测试**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_ibkr_readonly.py::test_search_symbol_keeps_default_smart_usd_contract \
  tests/test_ibkr_readonly.py::test_search_symbol_accepts_exchange_currency_and_primary_exchange -q
```

Expected:

- PASS（2 passed）

- [ ] **Step 3: 再运行 `tests/test_ibkr_readonly.py` 全量，确认没有回归**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_ibkr_readonly.py -q
```

Expected:

- PASS

- [ ] **Step 4: 提交实现**

```bash
git add scripts/ibkr_readonly.py tests/test_ibkr_readonly.py
git commit -m "feat: support configurable symbol search"
```

---

### Task 3: 用失败测试锁定余额明细格式化输出

**Files:**
- Modify: `tests/test_ibkr_readonly.py`
- Test: `tests/test_ibkr_readonly.py`

- [ ] **Step 1: 追加余额明细格式化红灯测试**

在 `tests/test_ibkr_readonly.py` 中追加：

```python
def test_format_balance_details_outputs_account_currency_lines():
    balance = {
        "TotalCashValue": [
            {"amount": 100.0, "currency": "USD", "account": "ACC-1"},
            {"amount": 800.0, "currency": "HKD", "account": "ACC-1"},
        ],
        "NetLiquidation": [
            {"amount": 1200.0, "currency": "USD", "account": "ACC-2"},
        ],
    }

    lines = ibkr_module.format_balance_details(balance)

    assert lines == [
        "   TotalCashValue | ACC-1 | USD: $100.00",
        "   TotalCashValue | ACC-1 | HKD: $800.00",
        "   NetLiquidation | ACC-2 | USD: $1,200.00",
    ]


def test_format_balance_details_returns_empty_list_when_no_entries():
    assert ibkr_module.format_balance_details({}) == []
```

- [ ] **Step 2: 运行红灯测试，确认当前模块还没有这个 helper**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_ibkr_readonly.py::test_format_balance_details_outputs_account_currency_lines \
  tests/test_ibkr_readonly.py::test_format_balance_details_returns_empty_list_when_no_entries -q
```

Expected:

- FAIL
- 失败原因包括 `format_balance_details` 尚不存在

- [ ] **Step 3: 提交红灯测试**

```bash
git add tests/test_ibkr_readonly.py
git commit -m "test: lock balance detail formatting"
```

---

### Task 4: 实现余额明细 helper 并接入 CLI

**Files:**
- Modify: `scripts/ibkr_readonly.py`
- Test: `tests/test_ibkr_readonly.py`

- [ ] **Step 1: 在 `scripts/ibkr_readonly.py` 中新增余额明细格式化 helper**

在 `format_pnl()` 之后、`main()` 之前加入：

```python
def format_balance_details(balance: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    lines: List[str] = []
    tag_order = ("TotalCashValue", "NetLiquidation")

    for tag in tag_order:
        for entry in balance.get(tag, []):
            amount = entry.get("amount")
            currency = entry.get("currency") or "N/A"
            account = entry.get("account") or "N/A"
            numeric_amount = parse_account_summary_value(amount)
            display_amount = (
                format_currency(numeric_amount)
                if numeric_amount is not None
                else str(amount)
            )
            lines.append(f"   {tag} | {account} | {currency}: {display_amount}")
    return lines
```

- [ ] **Step 2: 在 `main()` 中接入余额明细输出**

把 `main()` 中余额展示部分改成下面这样：

```python
    balance = client.get_balance()
    cash = get_primary_balance_amount(balance, "TotalCashValue")
    net_liq = get_primary_balance_amount(balance, "NetLiquidation")
    print(f"💵 现金余额: {format_currency(cash)}")
    print(f"💰 净资产: {format_currency(net_liq)}")

    detail_lines = format_balance_details(balance)
    if detail_lines:
        print("💼 余额明细:")
        for line in detail_lines:
            print(line)
    print("-" * 50)
```

- [ ] **Step 3: 重新运行余额明细测试，确认变绿**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_ibkr_readonly.py::test_format_balance_details_outputs_account_currency_lines \
  tests/test_ibkr_readonly.py::test_format_balance_details_returns_empty_list_when_no_entries -q
```

Expected:

- PASS（2 passed）

- [ ] **Step 4: 再跑 `tests/test_ibkr_readonly.py` 全量**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_ibkr_readonly.py -q
```

Expected:

- PASS

- [ ] **Step 5: 提交实现**

```bash
git add scripts/ibkr_readonly.py tests/test_ibkr_readonly.py
git commit -m "feat: show balance details in cli output"
```

---

### Task 5: 第 3 阶段最终验证

**Files:**
- Modify: `scripts/ibkr_readonly.py`
- Modify: `tests/test_ibkr_readonly.py`

- [ ] **Step 1: 跑第 3 阶段全量测试**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q
```

Expected:

- PASS（至少 27 passed）

- [ ] **Step 2: 跑 Python 语法校验**

Run:

```bash
python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py
```

Expected:

- 无输出
- 退出码 0

- [ ] **Step 3: 手动检查 `search_symbol()` 和余额明细 helper 已到位**

Run:

```bash
rg -n "build_stock_contract|primary_exchange|format_balance_details|余额明细" scripts/ibkr_readonly.py
```

Expected:

- 能看到上述标识都已存在

- [ ] **Step 4: 提交阶段收尾**

```bash
git add scripts/ibkr_readonly.py tests/test_ibkr_readonly.py
git commit -m "feat: complete phase3 query and display enhancements"
```

---

## Self-Review

### Spec Coverage

- 第 3 阶段的两大目标都被覆盖：
  - `search_symbol()` 从固定 `SMART/USD` 扩展为可配置参数
  - CLI 从只显示概览扩展到显示多账户/多币种余额明细

### Placeholder Scan

- 未使用模糊语句或缺失实现说明
- 每个任务都包含明确代码块、命令与期望结果

### Type Consistency

- contract 构造统一使用：
  - `build_stock_contract()`
  - `search_symbol(..., exchange, currency, primary_exchange)`
- 余额展示统一使用：
  - `get_primary_balance_amount()`
  - `format_balance_details()`

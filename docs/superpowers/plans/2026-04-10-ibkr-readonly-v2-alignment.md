# IBKR Read-Only v2 Alignment Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对齐 IBKR 只读仓库的安装链路、运行时行为、技能文档和自动化验证，修复 v2 架构下的关键一致性与可靠性问题。

**Architecture:** 保持当前“小仓库 + 单文件核心脚本”的形态不变，先用回归测试锁定连接行为、余额聚合和错误日志，再对 `scripts/ibkr_readonly.py` 做定点修复；随后替换旧版 `setup.sh`，并用文本测试约束 README、SKILL 与参考文档的 v2 一致性。

**Tech Stack:** Python 3.9+, ib_insync, requests, pytest, Bash

---

## File Structure

- `scripts/ibkr_readonly.py`
  - 继续作为唯一的查询客户端入口
  - 修复连接只读模式、重连后 market data type 设置、余额聚合、关键错误日志
- `scripts/setup.sh`
  - 完全替换为 v2 安装脚本
  - 只负责创建 venv、安装 `ib_insync` / `requests`、生成 `.env`、复制脚本
- `README.md`
  - 纠正运行说明、重连示例、部署目录示例
- `SKILL.md`
  - 移除绝对路径，改成相对路径/部署路径说明
  - 让技能说明与真实代码一致
- `references/api-endpoints.md`
  - 保留旧参考内容，但在文件头部明确声明“v1 已弃用参考”
- `requirements-dev.txt`
  - 只包含测试依赖
- `tests/test_ibkr_readonly.py`
  - 回归测试 `IBKRReadOnlyClient` 的核心修复
- `tests/test_repo_alignment.py`
  - 文本测试 setup / README / SKILL / reference 的仓库一致性

---

### Task 1: 建立测试基线并锁定连接只读行为

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/test_ibkr_readonly.py`
- Modify: `scripts/ibkr_readonly.py:21-24`
- Modify: `scripts/ibkr_readonly.py:80-102`

- [ ] **Step 1: 创建测试依赖文件**

```text
pytest>=8,<9
```

- [ ] **Step 2: 安装测试依赖**

Run: `python3 -m pip install -r requirements-dev.txt`
Expected: pip 成功安装 `pytest`

- [ ] **Step 3: 先写失败的连接回归测试**

在 `tests/test_ibkr_readonly.py` 先写下面这部分内容：

```python
from types import SimpleNamespace

import scripts.ibkr_readonly as ibkr_module


class FakeEvent(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self

    def clear(self):
        super().clear()


class FakeIB:
    def __init__(self):
        self.disconnectedEvent = FakeEvent()
        self.connect_calls = []
        self.market_data_types = []

    def connect(self, host, port, clientId, readonly=False):
        self.connect_calls.append(
            {
                "host": host,
                "port": port,
                "clientId": clientId,
                "readonly": readonly,
            }
        )

    def reqMarketDataType(self, market_data_type):
        self.market_data_types.append(market_data_type)


def build_client(monkeypatch):
    fake_ib = FakeIB()
    monkeypatch.setattr(ibkr_module, "IB", lambda: fake_ib)
    monkeypatch.setattr(ibkr_module.time, "sleep", lambda _: None)
    client = ibkr_module.IBKRReadOnlyClient(
        host="127.0.0.1",
        port=4001,
        client_id=7,
    )
    return client, fake_ib


def test_connect_uses_readonly_and_sets_delayed_market_data(monkeypatch):
    client, fake_ib = build_client(monkeypatch)

    assert client.connect() is True
    assert fake_ib.connect_calls == [
        {
            "host": "127.0.0.1",
            "port": 4001,
            "clientId": 7,
            "readonly": True,
        }
    ]
    assert fake_ib.market_data_types == [ibkr_module.MARKET_DATA_TYPE_DELAYED]


def test_disconnect_handler_reconnects_in_readonly_mode(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)
    reconnect_handler = fake_ib.disconnectedEvent[0]

    reconnect_handler()

    assert fake_ib.connect_calls == [
        {
            "host": "127.0.0.1",
            "port": 4001,
            "clientId": 7,
            "readonly": True,
        }
    ]
    assert fake_ib.market_data_types == [ibkr_module.MARKET_DATA_TYPE_DELAYED]
    assert "重连成功" in capsys.readouterr().out
```

- [ ] **Step 4: 运行测试，确认先红灯**

Run: `pytest tests/test_ibkr_readonly.py::test_connect_uses_readonly_and_sets_delayed_market_data tests/test_ibkr_readonly.py::test_disconnect_handler_reconnects_in_readonly_mode -q`
Expected: FAIL，失败点包含 `readonly` 断言不成立或 `MARKET_DATA_TYPE_DELAYED` 尚未定义

- [ ] **Step 5: 写最小实现让测试通过**

将 `scripts/ibkr_readonly.py` 的连接相关逻辑改成下面这样：

```python
# Configuration
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4001"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))
MARKET_DATA_TYPE_DELAYED = 3


class IBKRReadOnlyClient:
    def __init__(self, host: str = IB_HOST, port: int = IB_PORT, client_id: int = IB_CLIENT_ID):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self._setup_reconnect()

    def _apply_market_data_type(self):
        self.ib.reqMarketDataType(MARKET_DATA_TYPE_DELAYED)

    def _connect_gateway(self):
        self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=True)
        self._apply_market_data_type()

    def _setup_reconnect(self):
        """设置断线自动重连"""

        def on_disconnect():
            print(f"[{datetime.now():%H:%M:%S}] ⚠️ IB Gateway 断线，5秒后重连...")
            time.sleep(5)
            try:
                self._connect_gateway()
                print(f"[{datetime.now():%H:%M:%S}] ✅ 重连成功")
            except Exception as e:
                print(f"[{datetime.now():%H:%M:%S}] ❌ 重连失败: {e}")

        self.ib.disconnectedEvent += on_disconnect

    def connect(self) -> bool:
        """连接 IB Gateway"""
        try:
            self._connect_gateway()
            return True
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            return False
```

- [ ] **Step 6: 重新运行测试，确认变绿**

Run: `pytest tests/test_ibkr_readonly.py::test_connect_uses_readonly_and_sets_delayed_market_data tests/test_ibkr_readonly.py::test_disconnect_handler_reconnects_in_readonly_mode -q`
Expected: PASS（2 passed）

- [ ] **Step 7: 提交这一小步**

```bash
git add requirements-dev.txt tests/test_ibkr_readonly.py scripts/ibkr_readonly.py
git commit -m "test: cover readonly gateway connection flow"
```

---

### Task 2: 修复账户汇总聚合逻辑并稳定 CLI 输出

**Files:**
- Modify: `tests/test_ibkr_readonly.py`
- Modify: `scripts/ibkr_readonly.py:119-128`
- Modify: `scripts/ibkr_readonly.py:375-448`

- [ ] **Step 1: 追加失败的余额聚合测试**

在 `tests/test_ibkr_readonly.py` 追加以下测试：

```python
def test_get_balance_keeps_duplicate_tags_by_currency(monkeypatch):
    client, fake_ib = build_client(monkeypatch)
    fake_ib.accountSummary = lambda: [
        SimpleNamespace(tag="NetLiquidation", value="1000.50", currency="USD", account="U123"),
        SimpleNamespace(tag="NetLiquidation", value="8000", currency="HKD", account="U123"),
        SimpleNamespace(tag="TotalCashValue", value="bad-data", currency="USD", account="U123"),
    ]

    balance = client.get_balance()

    assert balance["NetLiquidation"] == [
        {"amount": 1000.5, "currency": "USD", "account": "U123"},
        {"amount": 8000.0, "currency": "HKD", "account": "U123"},
    ]
    assert balance["TotalCashValue"] == [
        {"amount": "bad-data", "currency": "USD", "account": "U123"},
    ]


def test_get_primary_balance_amount_prefers_first_numeric_value():
    balance = {
        "NetLiquidation": [
            {"amount": "N/A", "currency": "USD", "account": "U123"},
            {"amount": 1500.0, "currency": "USD", "account": "U123"},
        ]
    }

    assert ibkr_module.get_primary_balance_amount(balance, "NetLiquidation") == 1500.0
    assert ibkr_module.get_primary_balance_amount(balance, "MissingTag") == 0.0
```

- [ ] **Step 2: 运行测试，确认当前实现会覆盖数据**

Run: `pytest tests/test_ibkr_readonly.py::test_get_balance_keeps_duplicate_tags_by_currency tests/test_ibkr_readonly.py::test_get_primary_balance_amount_prefers_first_numeric_value -q`
Expected: FAIL，`get_balance()` 当前返回的不是列表聚合结构

- [ ] **Step 3: 写最小实现修复聚合和主函数读取**

把 `scripts/ibkr_readonly.py` 的余额逻辑改成下面这样：

```python
def parse_account_summary_value(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return value


def get_primary_balance_amount(balance: Dict[str, List[dict]], tag: str) -> float:
    entries = balance.get(tag, [])
    for entry in entries:
        amount = entry.get("amount")
        if isinstance(amount, (int, float)):
            return float(amount)
    return 0.0


class IBKRReadOnlyClient:
    def get_balance(self) -> Dict[str, List[dict]]:
        """获取账户余额/总结"""
        summary = self.ib.accountSummary()
        result: Dict[str, List[dict]] = {}
        for item in summary:
            result.setdefault(item.tag, []).append(
                {
                    "amount": parse_account_summary_value(item.value),
                    "currency": item.currency,
                    "account": getattr(item, "account", ""),
                }
            )
        return result


def main():
    """主函数 - 展示账户信息"""
    print("🏦 IBKR 投研辅助与只读查询工具 (ib_insync)")
    print("=" * 50)
    print("⚠️  安全模式：仅查询，无法执行任何交易操作")
    print("=" * 50)
    print()

    client = IBKRReadOnlyClient()

    if not client.connect():
        print("❌ 无法连接 IB Gateway。请确保：")
        print("   1. IB Gateway 已启动并登录")
        print("   2. API Settings 中已启用 Socket Clients")
        print(f"   3. 端口 {IB_PORT} 正确 (live=4001, paper=4002)")
        return

    print(f"✅ 已连接 IB Gateway ({client.host}:{client.port})")

    accounts = client.get_accounts()
    if accounts:
        print(f"📊 账户: {', '.join(accounts)}")

    balance = client.get_balance()
    cash = get_primary_balance_amount(balance, "TotalCashValue")
    net_liq = get_primary_balance_amount(balance, "NetLiquidation")
    print(f"💵 现金余额: {format_currency(cash)}")
    print(f"💰 净资产: {format_currency(net_liq)}")
    print("-" * 50)

    print("📈 当前持仓:")
    positions = client.get_positions()
    if not positions:
        print("   (无持仓)")
    else:
        for p in positions:
            pnl = format_pnl(p.unrealized_pnl, p.pnl_percent)
            print(f"   {p.symbol}: {p.quantity}股 @ {format_currency(p.avg_cost)} → 市值{format_currency(p.market_value)} {pnl}")
    print("-" * 50)

    print("🔍 测试获取 AAPL 行情...")
    quote = client.get_quote("AAPL")
    if quote:
        print(f"🍎 AAPL: ${quote.last_price:.2f} ({quote.change_pct:+.2f}%) | Bid: ${quote.bid:.2f} Ask: ${quote.ask:.2f}")
    else:
        print("❌ 获取行情失败")

    print("-" * 50)
    print("📰 测试获取 LMND 最新新闻...")
    news = client.get_company_news("LMND")
    if news:
        for idx, item in enumerate(news):
            print(f"  {idx+1}. [{item['date']}] {item['title']}")
    else:
        print("无最新新闻或获取失败。")

    client.disconnect()
    print("\n✅ 查询完成")
```

- [ ] **Step 4: 重新运行测试，确认变绿**

Run: `pytest tests/test_ibkr_readonly.py::test_get_balance_keeps_duplicate_tags_by_currency tests/test_ibkr_readonly.py::test_get_primary_balance_amount_prefers_first_numeric_value -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交这一小步**

```bash
git add tests/test_ibkr_readonly.py scripts/ibkr_readonly.py
git commit -m "fix: preserve duplicate IB account summary entries"
```

---

### Task 3: 去掉关键路径静默吞错并补日志回归测试

**Files:**
- Modify: `tests/test_ibkr_readonly.py`
- Modify: `scripts/ibkr_readonly.py:11-19`
- Modify: `scripts/ibkr_readonly.py:155-164`
- Modify: `scripts/ibkr_readonly.py:203-283`
- Modify: `scripts/ibkr_readonly.py:351-372`

- [ ] **Step 1: 追加失败的错误日志测试**

在 `tests/test_ibkr_readonly.py` 追加以下内容：

```python
import sys


def test_search_symbol_logs_contract_lookup_failure(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)

    def raise_lookup(_contract):
        raise RuntimeError("lookup exploded")

    fake_ib.qualifyContracts = raise_lookup

    assert client.search_symbol("AAPL") is None

    captured = capsys.readouterr()
    assert "search_symbol(AAPL)" in captured.err
    assert "lookup exploded" in captured.err


def test_get_fundamentals_logs_snapshot_failure_and_returns_partial_data(monkeypatch, capsys):
    client, fake_ib = build_client(monkeypatch)
    contract = SimpleNamespace(conId=99, description="Apple Inc", symbol="AAPL")
    monkeypatch.setattr(client, "search_symbol", lambda _symbol: contract)

    def raise_snapshot(_contract, _report_type):
        raise RuntimeError("snapshot down")

    fake_ib.reqFundamentalData = raise_snapshot
    fake_ib.reqTickers = lambda _contract: [SimpleNamespace(high=210.0, low=180.0)]

    fundamentals = client.get_fundamentals("AAPL")

    assert fundamentals is not None
    assert fundamentals.symbol == "AAPL"
    assert fundamentals.high_52w == "210.0"
    captured = capsys.readouterr()
    assert "get_fundamentals(AAPL)" in captured.err
    assert "snapshot down" in captured.err


def test_get_company_news_logs_request_failure(monkeypatch, capsys):
    client, _ = build_client(monkeypatch)

    class FakeRequests:
        @staticmethod
        def get(*_args, **_kwargs):
            raise RuntimeError("rss down")

    monkeypatch.setitem(sys.modules, "requests", FakeRequests)

    assert client.get_company_news("LMND") == []

    captured = capsys.readouterr()
    assert "get_company_news(LMND)" in captured.err
    assert "rss down" in captured.err
```

- [ ] **Step 2: 运行测试，确认先失败**

Run: `pytest tests/test_ibkr_readonly.py::test_search_symbol_logs_contract_lookup_failure tests/test_ibkr_readonly.py::test_get_fundamentals_logs_snapshot_failure_and_returns_partial_data tests/test_ibkr_readonly.py::test_get_company_news_logs_request_failure -q`
Expected: FAIL，当前实现大多是 `except Exception: pass`

- [ ] **Step 3: 写最小实现，把失败暴露出来**

把 `scripts/ibkr_readonly.py` 的相关片段改成下面这样：

```python
import os
import sys
import math
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Dict


def log_warning(context: str, error: Exception):
    print(f"⚠️ {context}: {error}", file=sys.stderr)


class IBKRReadOnlyClient:
    def search_symbol(self, symbol: str) -> Optional[Contract]:
        """搜索股票代码，返回 qualified Contract"""
        contract = Stock(symbol, 'SMART', 'USD')
        try:
            qualified = self.ib.qualifyContracts(contract)
            if qualified:
                return qualified[0]
        except Exception as e:
            log_warning(f"search_symbol({symbol})", e)
        return None

    def get_fundamentals(self, symbol: str) -> Optional[FundamentalData]:
        """获取个股基本面指标"""
        contract = self.search_symbol(symbol)
        if not contract:
            return None

        company_name = contract.description if hasattr(contract, 'description') else ""
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
            xml_data = self.ib.reqFundamentalData(contract, 'ReportSnapshot')
            if xml_data:
                root = ET.fromstring(xml_data)
                co_info = root.find('.//CoIDs')
                if co_info is not None:
                    name_el = root.find('.//CoGeneralInfo/CoName')
                    if name_el is not None:
                        company_name = name_el.text

                ind_el = root.find('.//Industry')
                if ind_el is not None:
                    industry = ind_el.get('type', '')
                    category = ind_el.text or ''

                for ratio in root.findall('.//Ratio'):
                    field_name = ratio.get('FieldName', '')
                    value = ratio.text or 'N/A'
                    if field_name == 'MKTCAP':
                        market_cap = value
                    elif field_name == 'PEEXCLXOR':
                        pe_ratio = value
                    elif field_name == 'TTMEPSXCLX':
                        eps = value
                    elif field_name == 'YIELD':
                        dividend_yield = value
                    elif field_name == 'NHIG':
                        high_52w = value
                    elif field_name == 'NLOW':
                        low_52w = value
                    elif field_name in {'APTS10DAVG', 'VOL10DAVG'}:
                        avg_volume = value
        except Exception as e:
            log_warning(f"get_fundamentals({symbol})", e)

        try:
            [ticker] = self.ib.reqTickers(contract)
            if high_52w == "N/A" and hasattr(ticker, 'high') and ticker.high:
                high_52w = str(ticker.high)
            if low_52w == "N/A" and hasattr(ticker, 'low') and ticker.low:
                low_52w = str(ticker.low)
        except Exception as e:
            log_warning(f"get_fundamentals({symbol}) ticker fallback", e)

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
            avg_volume=avg_volume
        )

    def get_company_news(self, symbol: str, limit: int = 5) -> List[dict]:
        """
        获取公司最新新闻 (Yahoo Finance RSS)
        IBKR News API 需要额外订阅，暂用免费源。
        """
        import requests

        try:
            url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                log_warning(f"get_company_news({symbol})", RuntimeError(f"unexpected status {r.status_code}"))
                return []

            root = ET.fromstring(r.text)
            news = []
            for item in root.findall(".//item")[:limit]:
                title = item.find("title").text if item.find("title") is not None else ""
                pubDate = item.find("pubDate").text if item.find("pubDate") is not None else ""
                link = item.find("link").text if item.find("link") is not None else ""
                news.append({"title": title, "date": pubDate, "link": link})
            return news
        except Exception as e:
            log_warning(f"get_company_news({symbol})", e)
            return []
```

- [ ] **Step 4: 重新运行测试，确认变绿**

Run: `pytest tests/test_ibkr_readonly.py::test_search_symbol_logs_contract_lookup_failure tests/test_ibkr_readonly.py::test_get_fundamentals_logs_snapshot_failure_and_returns_partial_data tests/test_ibkr_readonly.py::test_get_company_news_logs_request_failure -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交这一小步**

```bash
git add tests/test_ibkr_readonly.py scripts/ibkr_readonly.py
git commit -m "fix: surface readonly client lookup failures"
```

---

### Task 4: 重写 setup 脚本并用文本测试锁定仓库一致性

**Files:**
- Create: `tests/test_repo_alignment.py`
- Modify: `scripts/setup.sh:1-140`
- Modify: `README.md:94-156`
- Modify: `README.md:184-203`
- Modify: `README.md:245-295`
- Modify: `SKILL.md:34-45`
- Modify: `SKILL.md:56-98`
- Modify: `SKILL.md:141-146`
- Modify: `references/api-endpoints.md:1-20`

- [ ] **Step 1: 先写失败的仓库一致性测试**

创建 `tests/test_repo_alignment.py`：

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_setup_script_only_uses_v2_dependencies():
    text = read("scripts/setup.sh")
    for legacy in ("clientportal", "ibeam", "chromedriver", "Xvfb", "chromium-browser"):
        assert legacy not in text
    assert "ib_insync requests" in text
    assert "IB_HOST=127.0.0.1" in text


def test_skill_avoids_machine_specific_absolute_paths():
    text = read("SKILL.md")
    assert "/Users/" not in text
    assert "scripts/ibkr_readonly.py" in text


def test_reference_doc_is_marked_legacy():
    text = read("references/api-endpoints.md")
    assert "已弃用" in text
    assert "Client Portal" in text
    assert "v1" in text


def test_readme_deployed_tree_matches_v2_layout():
    text = read("README.md")
    assert "clientportal/" not in text
    assert "ibkr_readonly.py" in text
    assert "keepalive.py" in text
```

- [ ] **Step 2: 运行测试，确认先失败**

Run: `pytest tests/test_repo_alignment.py -q`
Expected: FAIL，因为当前 setup 仍包含 `clientportal` / `ibeam`，SKILL 仍有绝对路径，README 部署树仍带 `clientportal/`

- [ ] **Step 3: 用最小改动把 setup 和文档对齐到 v2**

先完整替换 `scripts/setup.sh`：

```bash
#!/bin/bash

set -euo pipefail

TRADING_DIR="${1:-$HOME/trading}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🏦 IBKR Read-Only v2 Setup"
echo "=========================="
echo "Installing to: $TRADING_DIR"
echo ""

require_cmd() {
    local cmd="$1"
    local help_text="$2"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "❌ Missing required command: $cmd"
        echo "   $help_text"
        exit 1
    fi
}

require_cmd python3 "Install Python 3.9+ first."
require_cmd java "Install Java 17+ first."

mkdir -p "$TRADING_DIR"
cd "$TRADING_DIR"

if [ ! -d "venv" ]; then
    echo "🐍 Creating Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install ib_insync requests

cp "$SCRIPT_DIR/ibkr_readonly.py" "$TRADING_DIR/ibkr_readonly.py"
cp "$SCRIPT_DIR/keepalive.py" "$TRADING_DIR/keepalive.py"

if [ ! -f ".env" ]; then
    cat > .env <<'EOF'
IB_HOST=127.0.0.1
IB_PORT=4001
IB_CLIENT_ID=1

# Optional Telegram alerts
TG_BOT_TOKEN=
TG_CHAT_ID=
EOF
    echo "✅ Created .env template"
else
    echo "✅ .env already exists"
fi

echo ""
echo "=========================================="
echo "✅ Setup complete!"
echo "Next steps:"
echo "1. Install and launch IB Gateway Stable manually"
echo "2. Log in with your read-only IBKR user"
echo "3. In IB Gateway, enable Socket Clients on port 4001"
echo "4. Keep Read-Only API unchecked to preserve query endpoints"
echo "5. Run: cd $TRADING_DIR && source venv/bin/activate && python ibkr_readonly.py"
echo "6. Optional: add keepalive.py to crontab for health checks"
echo "=========================================="
```

然后更新 `README.md` 的安装段落、重连示例和部署目录，替换成这些内容：

~~~md
### 第 1 步：安装依赖

```bash
brew install openjdk@17

mkdir -p ~/trading && cd ~/trading
python3 -m venv venv
source venv/bin/activate
pip install ib_insync requests
```

### 第 5 步：配置环境变量

创建 `~/trading/.env`：

```bash
IB_HOST=127.0.0.1
IB_PORT=4001
IB_CLIENT_ID=1

# Optional Telegram alerts
TG_BOT_TOKEN=
TG_CHAT_ID=
```

### 第 6 步：测试连接

```bash
cd ~/trading && source venv/bin/activate
python ibkr_readonly.py
```
~~~

~~~md
### ib_insync 自动重连

代码内置了断线自动重连逻辑，网络短暂中断后会自动恢复连接，并在重连后重新声明延迟行情模式：

```python
def _connect_gateway(self):
    self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=True)
    self.ib.reqMarketDataType(3)
```
~~~

~~~md
**部署后在 `~/trading/` 目录下的文件：**

```text
~/trading/
├── .env
├── ibkr_readonly.py
├── keepalive.py
├── keepalive.log
└── venv/
```
~~~

接着更新 `SKILL.md` 的 AI 助理规范与安全说明，替换成这些内容：

~~~md
1. **提取核心数据 (Data Anchoring)**
   - 优先执行仓库中的 `scripts/ibkr_readonly.py`，或部署后的 `~/trading/ibkr_readonly.py`，获取查询标的的最新基本面指标和新闻。
2. **强制全网深度检索 (Mandatory Web Search)**
   - 单靠 RSS 新闻不够，需要结合全网搜索最新宏观事件、财报会议记录、产品动态及行业竞品动作。
~~~

~~~md
### 1. 安装依赖

```bash
brew install openjdk@17
cd ~/trading
source venv/bin/activate
pip install ib_insync requests
```
~~~

~~~md
此技能设计为**完全只读**：
- 源代码中不包含任何下单 API 调用
- `IBKRReadOnlyClient` 连接时会显式传入 `readonly=True`
- 只读子账户本身也没有交易权限
- 即使有人要求下单，技能也无法执行
~~~

最后给 `references/api-endpoints.md` 文件头部加弃用声明：

~~~md
# IBKR Client Portal API Reference

> ⚠️ **已弃用 / Deprecated**：本文件仅保留给 v1 Client Portal Gateway 架构做历史参考。
> 当前仓库的实现基于 **IB Gateway + ib_insync**，不要把这里的 HTTP API 当成当前运行时依赖。

Base URL: `https://localhost:5000`
~~~

- [ ] **Step 4: 重新运行文本测试，确认变绿**

Run: `pytest tests/test_repo_alignment.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交这一小步**

```bash
git add tests/test_repo_alignment.py scripts/setup.sh README.md SKILL.md references/api-endpoints.md
git commit -m "fix: align setup and docs with readonly v2 flow"
```

---

### Task 5: 做最终验证并交付一个干净结果

**Files:**
- Modify: `tests/test_ibkr_readonly.py`
- Modify: `tests/test_repo_alignment.py`
- Modify: `scripts/ibkr_readonly.py`
- Modify: `scripts/setup.sh`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `references/api-endpoints.md`

- [ ] **Step 1: 跑完整测试集**

Run: `pytest tests/test_ibkr_readonly.py tests/test_repo_alignment.py -q`
Expected: PASS（11 passed）

- [ ] **Step 2: 跑 Python 语法校验**

Run: `python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py`
Expected: 无输出，退出码 0

- [ ] **Step 3: 检查 setup 与技能文件里是否还残留明显的旧链路关键字**

Run: `rg -n "clientportal|ibeam|chromedriver|Xvfb|/Users/" scripts/setup.sh SKILL.md`
Expected: 无输出

- [ ] **Step 4: 确认旧参考文档已经被标记为历史内容**

Run: `head -5 references/api-endpoints.md`
Expected:

```text
# IBKR Client Portal API Reference
> ⚠️ **已弃用 / Deprecated**：本文件仅保留给 v1 Client Portal Gateway 架构做历史参考。
> 当前仓库的实现基于 **IB Gateway + ib_insync**，不要把这里的 HTTP API 当成当前运行时依赖。
```

- [ ] **Step 5: 以最终修复提交收尾**

```bash
git add requirements-dev.txt tests/test_ibkr_readonly.py tests/test_repo_alignment.py scripts/ibkr_readonly.py scripts/setup.sh README.md SKILL.md references/api-endpoints.md
git commit -m "fix: align ibkr readonly runtime and docs with v2"
```

---

## Self-Review

### Spec Coverage

- P0 一致性修复：Task 4 + Task 5 覆盖
- P1 核心代码修复：Task 1 / Task 2 / Task 3 覆盖
- P2 验证闭环：Task 1 建测试依赖，Task 4 增文本测试，Task 5 做全量验证

### Placeholder Scan

- 未发现模糊步骤或缺失实现说明
- 每个代码步骤都给出明确代码块或命令

### Type Consistency

- `MARKET_DATA_TYPE_DELAYED` 在测试和实现中统一命名
- 余额读取统一使用 `get_primary_balance_amount`
- 错误日志统一使用 `log_warning`

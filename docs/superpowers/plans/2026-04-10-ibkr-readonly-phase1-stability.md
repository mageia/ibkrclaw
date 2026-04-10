# IBKR Read-Only Phase 1 Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提升只读客户端的断线恢复能力，并把 `keepalive.py` 从“进程/端口活着”升级为“查询链路真实可用”的健康检查。

**Architecture:** 保持当前脚本级结构，不引入后台服务或复杂调度器。先通过测试锁定“有限重连 + readiness 检查”的目标行为，再对 `scripts/ibkr_readonly.py` 和 `scripts/keepalive.py` 做小步重构，让连接恢复和健康状态判定都可测试、可验证。

**Tech Stack:** Python 3.9+, pytest, ib_insync, requests

---

## File Structure

- `scripts/ibkr_readonly.py`
  - 增加重连退避常量和 `_reconnect_with_backoff()`
  - 保持 `connect()`/`disconnect()` 接口不变
- `tests/test_ibkr_readonly.py`
  - 扩展 FakeIB，新增重连成功/失败路径测试
- `scripts/keepalive.py`
  - 拆出 `check_api_readiness()`、`evaluate_gateway_status()`、`send_transition_notification()`
  - 新增 `api_down` 状态
- `tests/test_keepalive.py`
  - 新增 keepalive 行为测试，覆盖 readiness 和状态迁移

---

### Task 1: 用失败测试锁定重连退避行为

**Files:**
- Modify: `tests/test_ibkr_readonly.py:18-177`
- Test: `tests/test_ibkr_readonly.py`

- [ ] **Step 1: 扩展测试桩，先让 FakeIB 支持连接失败序列**

把 `tests/test_ibkr_readonly.py` 里的 `FakeIB` 和 `build_client()` 改成下面这样：

```python
class FakeIB:
    def __init__(self, connect_outcomes=None):
        self.disconnectedEvent = FakeEvent()
        self.connect_calls = []
        self.market_data_types = []
        self.accountSummary = lambda: []
        self.connect_outcomes = list(connect_outcomes or [])

    def connect(self, host, port, clientId, readonly=False):
        self.connect_calls.append(
            {
                "host": host,
                "port": port,
                "clientId": clientId,
                "readonly": readonly,
            }
        )
        if self.connect_outcomes:
            outcome = self.connect_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome

    def reqMarketDataType(self, market_data_type):
        self.market_data_types.append(market_data_type)


def build_client(monkeypatch, connect_outcomes=None):
    fake_ib = FakeIB(connect_outcomes=connect_outcomes)
    monkeypatch.setattr(ibkr_module, "IB", lambda: fake_ib)
    client = ibkr_module.IBKRReadOnlyClient(host="127.0.0.1", port=4001, client_id=7)
    return client, fake_ib
```

- [ ] **Step 2: 追加两个失败测试，描述退避成功和达到上限**

在 `tests/test_ibkr_readonly.py` 追加下面两个测试：

```python
def test_disconnect_handler_retries_with_backoff_until_success(monkeypatch, capsys):
    sleep_calls = []
    monkeypatch.setattr(ibkr_module.time, "sleep", sleep_calls.append)
    client, fake_ib = build_client(
        monkeypatch,
        connect_outcomes=[
            RuntimeError("attempt-1"),
            RuntimeError("attempt-2"),
            None,
        ],
    )

    reconnect_handler = fake_ib.disconnectedEvent[0]
    reconnect_handler()

    assert len(fake_ib.connect_calls) == 3
    assert [call["readonly"] for call in fake_ib.connect_calls] == [True, True, True]
    assert fake_ib.market_data_types == [
        ibkr_module.MARKET_DATA_TYPE_DELAYED,
    ]
    assert sleep_calls == [
        ibkr_module.RECONNECT_BASE_DELAY_SECONDS * 1,
        ibkr_module.RECONNECT_BASE_DELAY_SECONDS * 2,
        ibkr_module.RECONNECT_BASE_DELAY_SECONDS * 3,
    ]
    captured = capsys.readouterr()
    assert "第 1 次重连失败" in captured.out
    assert "第 2 次重连失败" in captured.out
    assert "重连成功" in captured.out


def test_disconnect_handler_stops_after_max_attempts(monkeypatch, capsys):
    sleep_calls = []
    monkeypatch.setattr(ibkr_module.time, "sleep", sleep_calls.append)
    client, fake_ib = build_client(
        monkeypatch,
        connect_outcomes=[
            RuntimeError("still-down"),
            RuntimeError("still-down"),
            RuntimeError("still-down"),
        ],
    )

    reconnect_handler = fake_ib.disconnectedEvent[0]
    reconnect_handler()

    assert len(fake_ib.connect_calls) == ibkr_module.RECONNECT_MAX_ATTEMPTS
    assert sleep_calls == [
        ibkr_module.RECONNECT_BASE_DELAY_SECONDS * 1,
        ibkr_module.RECONNECT_BASE_DELAY_SECONDS * 2,
        ibkr_module.RECONNECT_BASE_DELAY_SECONDS * 3,
    ]
    captured = capsys.readouterr()
    assert "已达到最大重试次数" in captured.out
    assert "still-down" in captured.out
```

- [ ] **Step 3: 运行测试，确认现在是红灯**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_ibkr_readonly.py::test_disconnect_handler_retries_with_backoff_until_success \
  tests/test_ibkr_readonly.py::test_disconnect_handler_stops_after_max_attempts -q
```

Expected:

- FAIL
- 失败原因包括：
  - 模块还没有 `RECONNECT_BASE_DELAY_SECONDS`
  - 断线回调还没有多次重试或“达到最大重试次数”的输出

- [ ] **Step 4: 提交红灯测试**

```bash
git add tests/test_ibkr_readonly.py
git commit -m "test: lock reconnect backoff behavior"
```

---

### Task 2: 实现有限重试重连逻辑

**Files:**
- Modify: `scripts/ibkr_readonly.py:22-145`
- Test: `tests/test_ibkr_readonly.py`

- [ ] **Step 1: 先实现重连常量和退避 helper**

把 `scripts/ibkr_readonly.py` 的配置常量和连接逻辑改成下面这样：

```python
# Configuration
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4001"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))
MARKET_DATA_TYPE_DELAYED = 3
RECONNECT_BASE_DELAY_SECONDS = 1
RECONNECT_MAX_ATTEMPTS = 3


class IBKRReadOnlyClient:
    def __init__(self, host: str = IB_HOST, port: int = IB_PORT, client_id: int = IB_CLIENT_ID):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self._setup_reconnect()

    def _setup_reconnect(self):
        """设置断线自动重连"""

        def on_disconnect():
            print(f"[{datetime.now():%H:%M:%S}] ⚠️ IB Gateway 断线，开始自动重连...")
            if self._reconnect_with_backoff():
                print(f"[{datetime.now():%H:%M:%S}] ✅ 重连成功")
            else:
                print(f"[{datetime.now():%H:%M:%S}] ❌ 已达到最大重试次数，停止自动重连")

        self.ib.disconnectedEvent += on_disconnect

    def _apply_market_data_type(self):
        self.ib.reqMarketDataType(MARKET_DATA_TYPE_DELAYED)

    def _connect_gateway(self):
        self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=True)
        self._apply_market_data_type()

    def _reconnect_with_backoff(self) -> bool:
        for attempt in range(1, RECONNECT_MAX_ATTEMPTS + 1):
            delay_seconds = RECONNECT_BASE_DELAY_SECONDS * attempt
            time.sleep(delay_seconds)
            try:
                self._connect_gateway()
                return True
            except Exception as e:
                print(
                    f"[{datetime.now():%H:%M:%S}] ⚠️ 第 {attempt} 次重连失败: {e}"
                )
        return False

    def connect(self) -> bool:
        """连接 IB Gateway"""
        try:
            self._connect_gateway()
            return True
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            return False
```

- [ ] **Step 2: 重新运行刚才的两个测试**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_ibkr_readonly.py::test_disconnect_handler_retries_with_backoff_until_success \
  tests/test_ibkr_readonly.py::test_disconnect_handler_stops_after_max_attempts -q
```

Expected:

- PASS（2 passed）

- [ ] **Step 3: 再跑 `ibkr_readonly.py` 现有全部测试，确认没有回归**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_ibkr_readonly.py -q
```

Expected:

- PASS
- 原有 7 个测试仍通过，新测试一并通过

- [ ] **Step 4: 提交实现**

```bash
git add scripts/ibkr_readonly.py tests/test_ibkr_readonly.py
git commit -m "fix: add bounded reconnect backoff"
```

---

### Task 3: 用失败测试锁定 readiness 健康检查

**Files:**
- Create: `tests/test_keepalive.py`
- Test: `tests/test_keepalive.py`

- [ ] **Step 1: 新建 keepalive 测试文件**

创建 `tests/test_keepalive.py`：

```python
from importlib import util
from pathlib import Path


def _load_keepalive_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "keepalive.py"
    spec = util.spec_from_file_location("keepalive_module", script_path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


keepalive = _load_keepalive_module()


class FakeClient:
    def __init__(self, connect_result=True, accounts=None, error=None):
        self.connect_result = connect_result
        self.accounts = accounts if accounts is not None else ["ACC-1"]
        self.error = error
        self.disconnect_called = False

    def connect(self):
        if self.error is not None:
            raise self.error
        return self.connect_result

    def get_accounts(self):
        if self.error is not None:
            raise self.error
        return self.accounts

    def disconnect(self):
        self.disconnect_called = True


def test_evaluate_gateway_status_distinguishes_api_down():
    assert keepalive.evaluate_gateway_status(False, False, False) == "down"
    assert keepalive.evaluate_gateway_status(True, False, False) == "port_down"
    assert keepalive.evaluate_gateway_status(True, True, False) == "api_down"
    assert keepalive.evaluate_gateway_status(True, True, True) == "ok"


def test_check_api_readiness_returns_true_and_disconnects_client():
    fake_client = FakeClient(connect_result=True, accounts=["ACC-1"])

    result = keepalive.check_api_readiness(lambda: fake_client)

    assert result is True
    assert fake_client.disconnect_called is True


def test_check_api_readiness_returns_false_when_query_fails(capsys):
    fake_client = FakeClient(error=RuntimeError("api query failed"))

    result = keepalive.check_api_readiness(lambda: fake_client)

    assert result is False
    captured = capsys.readouterr()
    assert "API readiness check failed" in captured.out
    assert "api query failed" in captured.out


def test_main_sends_notification_when_state_changes_to_api_down(monkeypatch):
    sent_messages = []
    written_states = []

    monkeypatch.setattr(keepalive, "check_gateway_process", lambda: True)
    monkeypatch.setattr(keepalive, "check_socket_connection", lambda: True)
    monkeypatch.setattr(keepalive, "check_api_readiness", lambda client_factory=None: False)
    monkeypatch.setattr(keepalive, "read_state", lambda: "ok")
    monkeypatch.setattr(keepalive, "write_state", written_states.append)
    monkeypatch.setattr(keepalive, "send_telegram", sent_messages.append)
    monkeypatch.setattr(keepalive, "log", lambda _msg: None)

    keepalive.main()

    assert written_states == ["api_down"]
    assert len(sent_messages) == 1
    assert "API 不可用" in sent_messages[0]
```

- [ ] **Step 2: 先运行测试，确认现在是红灯**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_keepalive.py -q
```

Expected:

- FAIL
- 原因包括：
  - `keepalive.py` 还没有 `evaluate_gateway_status`
  - `keepalive.py` 还没有 `check_api_readiness`
  - `main()` 还不会产生 `api_down` 状态

- [ ] **Step 3: 提交红灯测试**

```bash
git add tests/test_keepalive.py
git commit -m "test: lock keepalive readiness behavior"
```

---

### Task 4: 实现 readiness 健康检查与状态迁移

**Files:**
- Modify: `scripts/keepalive.py:11-128`
- Test: `tests/test_keepalive.py`

- [ ] **Step 1: 重构 `keepalive.py` 为 readiness-aware 版本**

把 `scripts/keepalive.py` 改成下面这样：

```python
#!/usr/bin/env python3
"""
IB Gateway 健康检查脚本
每 5 分钟由 cron 执行，检查 IB Gateway 连接状态。
断线时发送 Telegram 通知。

Crontab entry:
*/5 * * * * cd ~/trading && venv/bin/python keepalive.py >> ~/trading/keepalive.log 2>&1
"""

import os
import socket
import subprocess
from datetime import datetime


IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4001"))

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gw_state")


def log(msg):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{timestamp} {msg}")


def check_gateway_process() -> bool:
    """检查 IB Gateway 进程是否存在"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ibgateway"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_socket_connection() -> bool:
    """检查 IB Gateway socket 端口是否可连"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((IB_HOST, IB_PORT))
        sock.close()
        return result == 0
    except Exception:
        return False


def build_readonly_client():
    from scripts.ibkr_readonly import IBKRReadOnlyClient

    return IBKRReadOnlyClient(host=IB_HOST, port=IB_PORT)


def check_api_readiness(client_factory=build_readonly_client) -> bool:
    """检查是否能完成最小只读查询"""
    client = None
    try:
        client = client_factory()
        if not client.connect():
            return False
        client.get_accounts()
        return True
    except Exception as e:
        log(f"⚠️ API readiness check failed: {e}")
        return False
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


def send_telegram(message: str):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        import requests

        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception as e:
        log(f"⚠️ Telegram 通知发送失败: {e}")


def read_state() -> str:
    """读取上次状态"""
    try:
        with open(STATE_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "unknown"


def write_state(state: str):
    """写入当前状态"""
    with open(STATE_FILE, "w") as f:
        f.write(state)


def evaluate_gateway_status(process_ok: bool, socket_ok: bool, api_ok: bool) -> str:
    if not process_ok:
        return "down"
    if not socket_ok:
        return "port_down"
    if not api_ok:
        return "api_down"
    return "ok"


def send_transition_notification(state: str):
    if state == "ok":
        send_telegram("✅ IB Gateway 已恢复连接！Agent 后台数据通道恢复。")
        return

    if state == "api_down":
        send_telegram(
            "⚠️ <b>IB Gateway API 不可用</b>\n"
            "进程和端口正常，但最小只读查询失败。\n"
            "请检查登录状态或 API 查询链路。"
        )
        return

    if state == "port_down":
        send_telegram(
            "⚠️ <b>IB Gateway 端口不通</b>\n"
            f"进程在运行，但 {IB_HOST}:{IB_PORT} 无法连接。\n"
            "可能原因：未登录 / 正在启动中\n"
            "请检查 IB Gateway 登录状态。"
        )
        return

    send_telegram(
        "❌ <b>IB Gateway 已停止</b>\n"
        "进程未运行，所有实盘数据查询不可用。\n"
        "请在 Mac mini 上重新启动 IB Gateway 并登录。"
    )


def main():
    process_ok = check_gateway_process()
    socket_ok = check_socket_connection() if process_ok else False
    api_ok = check_api_readiness() if process_ok and socket_ok else False
    current_state = evaluate_gateway_status(process_ok, socket_ok, api_ok)
    last_state = read_state()

    if current_state == "ok":
        if last_state != "ok":
            log("✅ IB Gateway 恢复正常")
            send_transition_notification("ok")
        else:
            log("✅ IB Gateway running - API reachable")
        write_state("ok")
        return

    if current_state == "api_down":
        log("⚠️ IB Gateway 进程和端口正常，但 API 查询失败")
        if last_state != "api_down":
            send_transition_notification("api_down")
        write_state("api_down")
        return

    if current_state == "port_down":
        log("⚠️ IB Gateway 进程在运行，但端口不通（可能需要登录）")
        if last_state != "port_down":
            send_transition_notification("port_down")
        write_state("port_down")
        return

    log("❌ IB Gateway 进程未运行")
    if last_state != "down":
        send_transition_notification("down")
    write_state("down")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行 keepalive 新测试，确认变绿**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_keepalive.py -q
```

Expected:

- PASS（4 passed）

- [ ] **Step 3: 再跑现有全量测试，确认没有回归**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q
```

Expected:

- PASS
- 所有旧测试和 keepalive 新测试一起通过

- [ ] **Step 4: 提交实现**

```bash
git add scripts/keepalive.py tests/test_keepalive.py
git commit -m "fix: add readiness-aware gateway health checks"
```

---

### Task 5: 阶段 1 最终验证

**Files:**
- Modify: `scripts/ibkr_readonly.py`
- Modify: `scripts/keepalive.py`
- Modify: `tests/test_ibkr_readonly.py`
- Create: `tests/test_keepalive.py`

- [ ] **Step 1: 跑阶段 1 全量测试**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q
```

Expected:

- PASS（至少 16 passed）

- [ ] **Step 2: 跑 Python 语法校验**

Run:

```bash
python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py
```

Expected:

- 无输出
- 退出码 0

- [ ] **Step 3: 手动检查 `keepalive.py` 已引入 `api_down` 分支**

Run:

```bash
rg -n "api_down|check_api_readiness|evaluate_gateway_status|send_transition_notification" scripts/keepalive.py
```

Expected:

- 能看到上述 4 个标识都已存在

- [ ] **Step 4: 提交阶段收尾**

```bash
git add scripts/ibkr_readonly.py scripts/keepalive.py tests/test_ibkr_readonly.py tests/test_keepalive.py
git commit -m "feat: complete phase1 readonly stability hardening"
```

---

## Self-Review

### Spec Coverage

- 阶段 1 的两大目标都被覆盖：
  - `ibkr_readonly.py` 有有限重试重连与恢复 market data type
  - `keepalive.py` 有 readiness 检查和 `api_down` 状态

### Placeholder Scan

- 未使用模糊语句或缺失实现说明
- 每个任务都包含明确测试命令和代码片段

### Type Consistency

- 重连相关名称统一使用：
  - `RECONNECT_BASE_DELAY_SECONDS`
  - `RECONNECT_MAX_ATTEMPTS`
  - `_reconnect_with_backoff()`
- keepalive 相关名称统一使用：
  - `check_api_readiness()`
  - `evaluate_gateway_status()`
  - `send_transition_notification()`

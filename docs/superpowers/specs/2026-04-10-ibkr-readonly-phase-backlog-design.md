# IBKR Read-Only 多阶段低优先级修复设计

## 背景

在完成 v2 架构一致性与关键可靠性问题修复后，仓库已经具备可验证、可维护的基础形态，但仍存在一批低优先级改进项。这些问题不会立即阻断使用，却会在稳定性、可观测性、查询范围和工程化能力上持续产生摩擦。

这批 backlog 主要集中在 3 个方向：

1. **稳定性层**
   - 重连逻辑仍是“单次 sleep + 重试”的够用实现
   - `keepalive.py` 仅检查进程和端口，不等于查询链路真实可用
2. **查询与展示层**
   - `search_symbol()` 仍写死 `SMART/USD`
   - CLI 对多账户/多币种余额只展示首个可用值
3. **工程化层**
   - `keepalive.py` 尚无自动化测试
   - 缺少正式 runtime 依赖文件
   - 缺少 CI 验证流程

用户已明确要求：**按阶段推进，每阶段都能独立交付**。

---

## 目标

1. 以低风险方式逐步补齐稳定性、工程化和查询体验上的剩余短板
2. 每个阶段都形成独立、可验证、可合并的交付单元
3. 保持只读边界和现有仓库体量，不把项目升级成复杂框架

## 非目标

- 不新增交易、改单、撤单能力
- 不引入常驻守护进程或复杂后台调度系统
- 不把脚本重构成大型 SDK 或服务端应用
- 不做真实 IB Gateway 集成测试自动化

---

## 分阶段交付顺序

用户已接受以下顺序：

1. **阶段 1：稳定性层**
2. **阶段 2：工程化层**
3. **阶段 3：查询与展示层**

该顺序的核心理由是：

- 先提高连接恢复与健康检查的真实性，避免在脆弱运行时之上继续叠功能
- 再把阶段 1 的行为沉淀进测试与 CI
- 最后在稳定基础之上扩展 symbol 查询与 CLI 展示

---

## 阶段 1：稳定性层

### 目标

让系统从“端口可连”提升到“断线可恢复、健康检查可判定真实查询可用性”。

### 设计

#### 1. 重连策略升级

当前重连逻辑是：

- 断线回调里 `sleep(5)`
- 单次重连
- 成功后直接打印日志
- 失败后仅打印一次

本阶段升级为**有限重试 + 明确日志 + 重连后恢复 market data type**，但仍保持单文件和最小复杂度。

新增明确常量：

- `MARKET_DATA_TYPE_DELAYED`
- `RECONNECT_BASE_DELAY_SECONDS`
- `RECONNECT_MAX_ATTEMPTS`

新增或调整的内部方法：

- `_apply_market_data_type()`
- `_connect_gateway()`
- `_reconnect_with_backoff()`

目标行为：

- 断线后按有限次数重试
- 每次失败输出清晰日志
- 成功后恢复延迟行情模式
- 达到上限后显式报告失败

#### 2. `keepalive.py` 升级为业务可用性检查

当前 `keepalive.py` 只做：

- 进程检查
- socket 连接检查

这不能区分：

- 端口通但未登录
- 端口通但 API 不可查
- 进程活着但查询链路不可用

本阶段升级为三级检查：

1. **Process**：`ibgateway` 进程存在
2. **Socket**：目标端口可连
3. **API Readiness**：通过最小只读查询确认链路可用

推荐 readiness 检查方式：

- 使用 `IBKRReadOnlyClient`
- 执行 `connect()`
- 调用 `get_accounts()` 或最小账户读取
- 成功则视为 ready

#### 3. `keepalive.py` 结构整理

为了可测试，不改变单文件前提下将逻辑拆为小函数：

- `check_gateway_process()`
- `check_socket_connection()`
- `check_api_readiness()`
- `evaluate_gateway_status()`
- `send_transition_notification()`

### 风险控制

- 不引入守护线程
- 不引入无限重试
- 不让健康检查执行重型查询
- 不新增隐式 fallback

### 测试策略

新增测试覆盖：

- 重连成功 / 失败 / 达到上限
- 重连成功后重新设置 market data type
- `keepalive.py` 对不同检查组合返回不同状态

---

## 阶段 2：工程化层

### 目标

让现有行为和阶段 1 行为具备可重复验证与自动保护能力。

### 设计

#### 1. runtime 依赖声明

新增正式运行时依赖文件：

- `requirements.txt`

内容保持最小：

- `ib_insync`
- `requests`

保留：

- `requirements-dev.txt` 作为测试依赖

#### 2. `keepalive.py` 自动化测试

新增：

- `tests/test_keepalive.py`

重点覆盖：

- 状态判断逻辑
- 状态迁移时是否发送通知
- 状态不变时是否避免重复通知
- readiness 检查失败时的分支

#### 3. CI

新增 GitHub Actions：

- 安装 Python
- 安装 `requirements.txt` 与 `requirements-dev.txt`
- 运行：
  - `pytest`
  - `python -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py`

CI 范围只限单元测试，不连接真实 IB Gateway。

### 风险控制

- 不做网络集成测试
- 不引入复杂 matrix
- 不把 CI 与个人本地环境耦合

### 测试策略

验收重点：

- 新环境能按依赖文件安装
- `keepalive.py` 有回归保护
- CI 能稳定跑通本地已验证的检查

---

## 阶段 3：查询与展示层

### 目标

在保持后向兼容的前提下，扩展 symbol 查询能力，并让 CLI 更准确展示多账户/多币种余额。

### 设计

#### 1. `search_symbol()` 扩展

当前实现固定：

```python
Stock(symbol, "SMART", "USD")
```

本阶段不尝试构建“万能合约搜索器”，而是做兼容增强：

- 保留默认 `SMART/USD`
- 支持可选参数：
  - `exchange`
  - `currency`
  - `primary_exchange`

从而实现：

- 旧调用保持可用
- 新调用可覆盖更多市场场景

#### 2. CLI 余额展示增强

当前内部数据结构已经保留多账户/多币种信息，但 CLI 只展示首个可用值。

本阶段改为：

- 顶部继续保留“现金余额 / 净资产”概览
- 新增“账户余额明细”区块
- 以 `tag + account + currency` 维度打印摘要

这样既不破坏原有简洁输出，又能正确暴露真实数据结构。

### 风险控制

- 不扩展到全部合约类型
- 不改动 scanner、news 等其它接口
- 不改变默认行为的兼容性

### 测试策略

新增测试覆盖：

- `search_symbol()` 默认行为
- `search_symbol()` 在指定 exchange/currency/primary_exchange 时构造正确
- CLI 余额明细格式化输出

---

## 文件范围（按阶段）

### 阶段 1

- 修改：`scripts/ibkr_readonly.py`
- 修改：`scripts/keepalive.py`
- 修改：`tests/test_ibkr_readonly.py`
- 新增：`tests/test_keepalive.py`

### 阶段 2

- 新增：`requirements.txt`
- 修改：`requirements-dev.txt`（如需要）
- 新增：`.github/workflows/python-tests.yml`
- 修改：`tests/test_keepalive.py`

### 阶段 3

- 修改：`scripts/ibkr_readonly.py`
- 修改：`tests/test_ibkr_readonly.py`
- 修改：`README.md`（如 CLI 用法说明需要同步）

---

## 统一验收标准

每个阶段完成时都必须满足：

1. 对应阶段的新增/修改测试通过
2. 总测试集通过
3. `python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py` 通过
4. 不引入新的旧架构残留
5. 不破坏只读边界

---

## 为什么这样拆分

这个拆分兼顾了：

- **风险隔离**：稳定性、工程化、查询体验分别交付
- **上下文控制**：每阶段只聚焦一类问题
- **验证清晰**：每阶段都有明确测试目标
- **后向兼容**：先稳运行时，再扩能力

相比“一轮全做”，这种方式更适合当前仓库体量与用户要求。

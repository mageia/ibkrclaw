# IBKR Read-Only v2 一致性与可靠性修复设计

## 背景

当前仓库的核心查询脚本已经迁移到 **IB Gateway + ib_insync** 的 v2 架构，但仓库外围仍残留明显的 v1 痕迹，导致“文档、技能描述、安装脚本、代码行为”四者不一致。最典型的问题包括：

- `scripts/setup.sh` 仍在安装 Client Portal Gateway、`ibeam`、Xvfb、Chrome 等旧链路依赖
- `README.md` / `SKILL.md` 声称连接启用了 `readonly=True`，但代码实际未传该参数
- `SKILL.md` 使用了不可移植的绝对路径
- `get_balance()` 会覆盖重复 tag，丢失多账户/多币种信息
- 多个关键路径存在 `except Exception: pass`，故障不可观测

本次修复的目标不是扩展新能力，而是让仓库在 **声明、安装、运行、验证** 四个层面重新对齐。

---

## 目标

1. 让仓库的安装方式、架构说明、技能说明与实际代码保持一致
2. 修复核心只读查询脚本中会影响正确性或排障效率的问题
3. 建立最小测试闭环，避免相同问题再次回归

## 非目标

- 不新增交易、改单、撤单能力
- 不把脚本重构成大型 SDK
- 不把 keepalive 改造成复杂常驻服务
- 不处理超出当前审查结论之外的长期工程化议题

---

## 修复范围

### 文件范围

- 修改：`scripts/setup.sh`
- 修改：`scripts/ibkr_readonly.py`
- 修改：`README.md`
- 修改：`SKILL.md`
- 修改：`references/api-endpoints.md`
- 新增：`tests/test_ibkr_readonly.py`

如实现时需要声明测试依赖，可补充：

- 新增：`requirements-dev.txt`

---

## 设计方案

### 1. 安装链路统一到 v2 架构

`scripts/setup.sh` 将完全围绕 **IB Gateway + ib_insync** 重新编写，不再下载 Client Portal Gateway，也不再依赖 `ibeam`、Chrome、Chromedriver、Xvfb。

新脚本职责仅保留：

- 检查 Python 3 与 Java
- 创建 `TRADING_DIR`
- 创建 Python 虚拟环境
- 安装 `ib_insync` 与 `requests`
- 生成与 v2 一致的 `.env` 模板（仅 `IB_HOST`、`IB_PORT`、`IB_CLIENT_ID`、可选 Telegram 配置）
- 输出清晰的后续步骤：手动安装并登录 IB Gateway、设置 API、执行查询脚本、配置 keepalive

这样可以让安装脚本与 README 中的 v2 说明一致，避免用户被引导到已经弃用的旧方案。

### 2. 文档与技能说明对齐真实实现

`README.md`、`SKILL.md`、`references/api-endpoints.md` 将同步调整：

- 删除或改写所有暗示当前仍使用 Client Portal / `ibeam` 的内容
- 去掉“当前代码已启用 `readonly=True`”这种与实现不一致的表述
- 把 `SKILL.md` 中的绝对路径替换为仓库相对路径或“部署后的脚本路径”说明
- 明确 `references/api-endpoints.md` 是 **v1 已弃用参考**，不再作为当前实现依据

这样做的重点不是美化文档，而是避免用户在部署、调试和审计时被错误信息误导。

### 3. 核心查询脚本提升可观测性与正确性

`scripts/ibkr_readonly.py` 保持单文件结构，不做大拆分，但做以下定点修复：

#### 3.1 连接与重连行为

- `connect()` 显式传入 `readonly=True`，使代码与仓库对外承诺一致
- 重连逻辑沿用现有 `disconnectedEvent` 机制，但重连成功后重新设置市场数据类型
- 断连与重连失败时输出明确日志，不再静默吞错

#### 3.2 余额数据结构

`get_balance()` 由当前“按 tag 覆盖”的结构改为“按 tag 聚合多个条目”的结构，至少保留：

- `amount`
- `currency`
- `account`

建议返回结构：

```python
{
    "NetLiquidation": [
        {"amount": 1000.0, "currency": "USD", "account": "U123"},
        {"amount": 800.0, "currency": "HKD", "account": "U123"},
    ],
    ...
}
```

这样即使同一 tag 在多账户或多币种下重复出现，也不会丢失信息。

`main()` 中展示余额时则读取聚合后的首选值；若没有唯一值，按第一条可用数据展示，并保持输出稳定。

#### 3.3 异常处理

以下方法不再使用无声失败：

- `search_symbol()`
- `get_fundamentals()`
- `get_company_news()`

策略为：

- 对外接口仍保持“失败返回 `None` / 空列表”的简洁行为
- 但失败原因必须输出到 stderr 或标准日志，至少包含函数名、symbol 和异常信息

这样既不会打断调用方流程，也不会隐藏根因。

---

## 数据流设计

### 查询链路

1. 调用方创建 `IBKRReadOnlyClient`
2. `connect()` 建立到 IB Gateway 的只读连接
3. 查询方法通过 `ib_insync` 拉取账户、持仓、行情、扫描器或历史数据
4. 基本面与新闻查询在外部依赖失败时返回空值，但同步输出错误日志
5. `disconnect()` 显式断开，并移除自动重连 handler，避免主动退出后误重连

### 健康检查链路

本次不改变 `keepalive.py` 的总体职责，仅保持其与文档一致：

1. 检查进程
2. 检查 socket 端口
3. 状态变化时写入 `.gw_state`
4. 发送 Telegram 通知

---

## 错误处理原则

本次修复遵循“暴露失败而非静默降级”：

- 不新增 mock 成功路径
- 不引入静默 fallback
- 对查询失败保持返回空值/`None` 的外部契约，但必须记录失败原因
- 对安装脚本中的关键依赖缺失直接失败退出，给出明确指引

---

## 测试设计

采用最小闭环测试，不依赖真实 IB Gateway。

### 测试策略

使用 fake/stub `IB` 对象覆盖以下行为：

1. `get_balance()` 不覆盖重复 tag，能保留多币种/多账户条目
2. `connect()` 建立连接时启用只读参数，并设置 market data type
3. `search_symbol()` / `get_company_news()` 失败时会输出错误，而不是完全吞掉

### 测试边界

- 不连接真实 IB Gateway
- 不验证外部 Yahoo RSS 的在线可用性
- 不做端到端集成测试

测试目标是保护本次修复的关键行为，而不是模拟完整券商环境。

---

## 验收标准

满足以下条件才算本次修复完成：

1. `scripts/setup.sh` 不再包含 Client Portal / `ibeam` / Chrome / Xvfb 安装逻辑
2. `README.md`、`SKILL.md`、`references/api-endpoints.md` 与 v2 架构表述一致
3. `IBKRReadOnlyClient.connect()` 明确使用只读连接
4. `get_balance()` 不再覆盖重复 summary 条目
5. 关键失败路径会输出可诊断错误信息
6. 新增自动化测试覆盖上述关键行为
7. `python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py` 通过
8. 测试命令通过

---

## 风险与兼容性

### 风险

- `get_balance()` 返回结构变化后，现有依赖该结构的调用方可能需要同步适配
- `readonly=True` 在部分 API 场景下可能暴露出此前未被文档说明的限制

### 控制方式

- 只在仓库内部脚本与文档范围内调整，不做额外 API 扩张
- `main()` 保持可运行输出，降低结构变化对演示脚本的影响
- 测试覆盖新旧边界最敏感的位置

---

## 实施顺序

### P0：一致性修复

1. 重写 `scripts/setup.sh`
2. 修正文档和技能说明
3. 标记旧 API 参考文档为弃用

### P1：核心代码修复

1. 修复连接只读参数与重连后的 market data 设置
2. 修复 `get_balance()` 聚合逻辑
3. 修复关键路径静默吞错

### P2：验证闭环

1. 新增轻量测试
2. 运行语法校验与测试
3. 确认文档、代码、安装链路三者一致

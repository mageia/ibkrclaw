# IBKR Trading Client 交易客户端设计

## 背景

当前仓库只有 `scripts/ibkr_readonly.py`，其定位是只读查询客户端，能力覆盖连接、余额、持仓、行情、基本面、历史数据、扫描器与新闻查询，但明确不支持任何交易写操作。

本次需求是在保持现有仓库结构简单的前提下，新增一个“完全体”的下单助手类，支持：

- 下单
- 撤单
- 改单
- 查询订单
- 查询成交

同时，新的交易客户端不能依赖 `scripts/ibkr_readonly.py`。需要的公共逻辑可以复制出来，但 `scripts/ibkr_trading.py` 必须是一个**单文件自洽实现**，未来可以直接作为 `scripts/ibkr_readonly.py` 的功能超集替代品。

---

## 目标

1. 新增 `scripts/ibkr_trading.py`，提供独立的 `IBKRTradingClient`
2. 在一个文件中同时覆盖当前只读查询能力与新增交易能力
3. 提供两层 API：
   - 原生底层接口：直接接收 `ib_insync` 的 `Contract` / `Order`
   - 高层便捷接口：接收项目内定义的参数对象
4. 高层接口首版支持常见单腿订单，并尽量通用到股票、ETF、期权、期货
5. 保持失败显式可见，不引入 silent fallback、mock success 或静默降级

## 非目标

1. 不让 `ibkr_trading.py` import `ibkr_readonly.py`
2. 不在首版实现 bracket、combo、algo、条件单等复杂订单的高层 DSL
3. 不新增风控限额、自动拒单、交易策略引擎
4. 不把项目重构成多文件 SDK 或大型 package
5. 不连接真实 IB Gateway 做集成测试

---

## 文件范围

### 新增文件

- `scripts/ibkr_trading.py`
- `tests/test_ibkr_trading.py`

### 本阶段不要求修改

- `scripts/ibkr_readonly.py`

后续如果项目决定全面切换，可以再统一把调用方替换到 `scripts/ibkr_trading.py`。

---

## 核心设计结论

### 1. 单文件独立实现

`IBKRTradingClient` 直接定义在 `scripts/ibkr_trading.py` 中，文件内包含：

- 连接配置常量
- dataclass 数据模型
- 公共 helper 函数
- 查询逻辑
- 合约构建逻辑
- 订单构建逻辑
- 下单 / 撤单 / 改单逻辑
- 订单 / 成交查询逻辑
- 可选的 `main()` 演示入口

这样可以保证该文件不依赖 `ibkr_readonly.py`，并能逐步替代旧文件成为新的主客户端入口。

### 2. 保持“查询 + 交易”双能力

新文件不仅新增交易写操作，还会复制并保留现有只读脚本的核心查询能力，使其成为功能超集：

- 获取账户
- 获取余额
- 获取持仓
- 搜索/校验合约
- 获取行情
- 获取基本面
- 获取历史数据
- 市场扫描
- 新闻查询

### 3. 两层 API 并存

#### 原生底层接口

面向熟悉 `ib_insync` 的调用方，直接传入：

- `Contract`
- `Order`
- `Trade`

示例方法：

- `place_order_raw(contract, order)`
- `cancel_order_raw(order_or_trade)`
- `modify_order_raw(contract, order)`
- `get_open_orders_raw()`
- `get_orders_raw()`
- `get_trades_raw()`
- `get_fills_raw()`

该层目标是保留最大通用性，不人为限制复杂合约或高级订单能力。

#### 高层便捷接口

面向业务代码与 agent 使用，提供标准化参数对象和标准化返回结构。示例方法：

- `build_contract(spec)`
- `qualify_contract(spec)`
- `build_order(request)`
- `place_order(request)`
- `cancel_order(order_id)`
- `modify_order(request)`
- `get_open_orders()`
- `get_orders()`
- `get_trades()`
- `get_fills()`

高层接口负责：

- 参数校验
- 原生对象构建
- `qualifyContracts`
- 标准化快照转换

---

## 数据模型设计

### 合约模型：`ContractSpec`

用于高层接口统一描述股票、ETF、期权、期货合约。建议字段：

- `sec_type`: `STK | OPT | FUT`
- `symbol`
- `exchange`
- `currency`
- `primary_exchange`
- `last_trade_date_or_contract_month`
- `strike`
- `right`
- `multiplier`
- `local_symbol`
- `trading_class`
- `con_id`

字段策略：

- `STK` / ETF：至少需要 `symbol`，其余字段按需传入
- `OPT`：需要到期日、行权价、`right`（`C` / `P`）
- `FUT`：需要合约月份，必要时允许传 `local_symbol`
- 如果调用方已经掌握完整 `Contract` 构造方式，可直接使用 raw 接口，不必强行适配高层对象

### 下单模型：`OrderRequest`

建议字段：

- `contract: ContractSpec`
- `action: BUY | SELL`
- `quantity`
- `order_type: MKT | LMT | STP | STP_LMT`
- `limit_price`
- `stop_price`
- `tif`
- `outside_rth`
- `account`
- `transmit`

### 改单模型：`ModifyOrderRequest`

建议字段：

- `order_id`
- `quantity`
- `limit_price`
- `stop_price`
- `tif`
- `outside_rth`
- `transmit`

说明：

- `order_id` 为必填
- 其余字段全部为可选，仅覆盖调用方明确想修改的部分
- 首版只支持修改常见可变字段，不在高层接口暴露所有 IB 订单属性

### 标准化返回模型

建议新增：

- `OrderSnapshot`
- `TradeSnapshot`
- `FillSnapshot`

快照对象的目标不是完整镜像所有 `ib_insync` 字段，而是输出业务侧最常用的稳定字段，例如：

- `order_id`
- `perm_id`
- `symbol`
- `sec_type`
- `action`
- `order_type`
- `total_quantity`
- `limit_price`
- `stop_price`
- `status`
- `filled`
- `remaining`
- `avg_fill_price`
- `last_fill_price`
- `exchange`
- `account`
- `time`

---

## 类职责设计

### `IBKRTradingClient`

`IBKRTradingClient` 是 `scripts/ibkr_trading.py` 中唯一面向外部的核心客户端类，负责：

1. 连接/断开到 IB Gateway
2. 断线自动重连
3. 设置市场数据类型（延迟行情兜底）
4. 查询账户、余额、持仓、行情、基本面、历史数据、scanner、新闻
5. 构建与校验合约
6. 构建订单
7. 下单、撤单、改单
8. 查询 open orders / orders / trades / fills
9. 将原生对象转换为标准化快照

该类不再带“只读”语义；连接时显式采用可交易模式。

---

## 连接与重连行为

### 连接模式

`IBKRTradingClient.connect()` 将调用：

```python
ib.connect(host, port, clientId=client_id, readonly=False)
```

与当前只读客户端不同，新类必须显式启用可交易连接。

### 重连逻辑

保留与现有实现相近的 `disconnectedEvent` 自动重连模式：

1. 断线时输出明确日志
2. 等待固定时间后重连
3. 重连成功后重新设置 market data type
4. 重连失败时输出明确错误，不吞异常细节

### 主动断开

`disconnect()` 需要先移除重连 handler，再执行断开，避免用户主动退出后又被自动拉起重连。

---

## 查询能力设计

为保证未来可以替代 `ibkr_readonly.py`，新文件会完整保留以下查询能力：

- `get_accounts()`
- `get_balance()`
- `get_positions()`
- `search_symbol()` 或更通用的合约查找能力
- `get_quote()`
- `get_fundamentals()`
- `get_historical_data()`
- `run_scanner()`
- `get_company_news()`

实现策略：

- 逻辑允许直接复制自 `ibkr_readonly.py`
- helper 与 dataclass 同步复制到新文件
- 查询返回结构尽量保持与现有只读脚本一致，例如继续复用 `Position`、`Quote`、`FundamentalData` 等数据模型
- 行为尽量与现有只读脚本保持一致，避免未来替换时出现语义漂移

---

## 合约构建设计

### 高层构建：`build_contract(spec: ContractSpec)`

根据 `sec_type` 构造不同原生合约：

- `STK` -> `Stock(...)`
- `OPT` -> `Option(...)`
- `FUT` -> `Future(...)`

约束：

- 缺少关键字段时直接抛异常
- 不用隐式默认值偷偷补齐业务关键参数
- `qualifyContracts` 前后都保留清晰错误上下文

### 高层校验：`qualify_contract(spec: ContractSpec)`

行为：

1. 调用 `build_contract()`
2. 执行 `ib.qualifyContracts(...)`
3. 若返回为空，显式抛错
4. 返回第一个 qualified contract

### 底层校验

raw 接口也建议在下单前调用 `qualifyContracts`，但不擅自重写调用方传入的高级结构。

---

## 订单构建设计

### 高层构建：`build_order(request: OrderRequest)`

首版支持以下常见单腿订单：

- `MKT`
- `LMT`
- `STP`
- `STP_LMT`

字段要求：

- `MKT`：不允许依赖 `limit_price` / `stop_price`
- `LMT`：必须提供 `limit_price`
- `STP`：必须提供 `stop_price`
- `STP_LMT`：必须同时提供 `stop_price` 和 `limit_price`

公共字段同步设置：

- `action`
- `totalQuantity`
- `tif`
- `outsideRth`
- `account`
- `transmit`

如果业务需要复杂订单：

- bracket
- combo
- algo
- 条件单

则直接使用 raw 接口，不在首版高层封装里模拟支持。

---

## 订单生命周期设计

### 1. 下单

#### 高层接口：`place_order(request: OrderRequest)`

内部步骤：

1. 校验 `OrderRequest`
2. 构建并 qualify contract
3. 构建 `Order`
4. 调用 `ib.placeOrder(contract, order)`
5. 将返回的 `Trade` 转换为 `TradeSnapshot`

#### 底层接口：`place_order_raw(contract: Contract, order: Order)`

内部步骤：

1. 对 contract 执行 `qualifyContracts`
2. 调用 `ib.placeOrder(contract, order)`
3. 返回原始 `Trade`

### 2. 撤单

#### 高层接口：`cancel_order(order_id: int)`

内部步骤：

1. 从当前未完成订单或 trade 集合中定位 `order_id`
2. 找到对应 `Trade` / `Order`
3. 调用 `ib.cancelOrder(order)`
4. 返回最新 `TradeSnapshot`
5. 如果找不到订单，直接抛错，不返回伪成功结果

#### 底层接口：`cancel_order_raw(order_or_trade)`

行为：

- 传入 `Trade` 时取其 `order`
- 传入 `Order` 时直接撤单
- 返回原始 `Trade` 或 `None`

### 3. 改单

#### 高层接口：`modify_order(request: ModifyOrderRequest)`

内部步骤：

1. 根据 `order_id` 查找现有未完成订单
2. 读取其原始 `contract` 与 `order`
3. 仅更新请求中明确提供的字段
4. 使用原 `contract + 更新后的 order` 再次调用 `placeOrder`
5. 返回新的 `TradeSnapshot`

限制：

- 首版仅支持修改数量、限价、止损价、TIF、盘前盘后、transmit 等常见字段
- 如需更复杂改单，使用 raw 接口

#### 底层接口：`modify_order_raw(contract, order)`

行为：

- 调用方提供完整 `Contract` / `Order`
- 直接重新 `placeOrder(contract, order)`
- 返回原始 `Trade`

### 4. 查询

#### 标准化查询

- `get_open_orders()` -> `list[OrderSnapshot]`
- `get_orders()` -> `list[OrderSnapshot]`
- `get_trades()` -> `list[TradeSnapshot]`
- `get_fills()` -> `list[FillSnapshot]`

#### 原生查询

- `get_open_orders_raw()` -> 原始对象列表
- `get_orders_raw()` -> 原始对象列表
- `get_trades_raw()` -> 原始对象列表
- `get_fills_raw()` -> 原始对象列表

---

## 错误处理原则

遵循仓库已有约束：

- 不增加 silent fallback
- 不增加 mock/simulation success path
- 不用“为了不报错”而吞掉异常
- 查询或交易失败时要暴露明确上下文

具体行为：

1. 参数缺失或不合法：直接抛 `ValueError` 等明确异常
2. 合约无法 qualify：直接抛异常，并带上 `symbol` / `sec_type` 上下文
3. 找不到要撤/改的订单：直接抛异常
4. 外部依赖失败：交易写接口一律继续抛出异常；查询接口保持与现有只读脚本一致的外部契约（例如 `search_symbol()` 返回 `None`、`get_historical_data()` 返回空列表、`get_company_news()` 返回空列表），但必须同步输出错误日志，不能无痕失败

---

## 测试设计

### 新增测试文件

- `tests/test_ibkr_trading.py`

### 测试策略

沿用仓库当前模式，不连接真实 IB Gateway，而是 stub `ib_insync` 行为。

### 必测行为

#### 连接与重连

1. `connect()` 使用 `readonly=False`
2. 自动重连后仍使用 `readonly=False`
3. 连接与重连后都会设置 `MARKET_DATA_TYPE_DELAYED`

#### 查询能力回归

1. `get_balance()` 保留重复 tag 条目
2. `search_symbol()` / 合约 qualify 失败时有错误上下文
3. `get_fundamentals()` 在快照失败时能保留 fallback 行为与日志
4. `get_company_news()` 请求失败时有错误上下文

#### 高层交易接口

1. `build_contract()` 能正确构建 `STK` / `OPT` / `FUT`
2. `build_order()` 能正确构建 `MKT` / `LMT` / `STP` / `STP_LMT`
3. `place_order()` 会先 qualify 再下单
4. `cancel_order()` 会按 `order_id` 查找并撤单
5. `modify_order()` 会在原订单基础上覆盖指定字段并重新提交
6. `get_open_orders()` / `get_trades()` / `get_fills()` 能输出标准化结果

#### 底层原生接口

1. `place_order_raw()` 可直接提交原生对象
2. `cancel_order_raw()` 接受 `Order` 或 `Trade`
3. `modify_order_raw()` 直接转发给 `placeOrder`
4. `get_*_raw()` 返回未封装的原始数据

### 测试边界

- 不做真实 IB 联调
- 不做在线新闻源可用性测试
- 不做复杂订单高层封装测试，因为该能力不在本次范围内

---

## 验收标准

满足以下条件即可视为本次设计落地成功：

1. 新增 `scripts/ibkr_trading.py`
2. 文件不依赖 `scripts/ibkr_readonly.py`
3. `IBKRTradingClient` 同时具备查询与交易能力
4. 连接采用 `readonly=False`
5. 同时提供 raw 接口与高层便捷接口
6. 高层接口首版支持 `STK` / `OPT` / `FUT` 与 `MKT` / `LMT` / `STP` / `STP_LMT`
7. 下单、撤单、改单、订单查询、成交查询都有自动化测试覆盖
8. 新测试可在本地 fake/stub 环境下稳定执行

---

## 风险与控制

### 风险 1：复制查询逻辑后与只读文件逐渐漂移

控制方式：

- 首版先确保 `ibkr_trading.py` 足以独立替代 `ibkr_readonly.py`
- 后续如果全面切换，再删除旧文件或改由旧文件包装新文件

### 风险 2：高层封装过度扩张，变成难维护的大而全接口

控制方式：

- 高层封装仅覆盖最常见合约与订单
- 复杂场景统一走 raw 接口

### 风险 3：改单逻辑与 IB API 实际行为存在理解偏差

控制方式：

- 测试中显式验证“按已有 order 更新字段并重新 placeOrder”的实现路径
- 不在首版做隐式自动推断或魔法行为

---

## 实施顺序建议

### P0：建立新文件骨架

1. 新建 `scripts/ibkr_trading.py`
2. 复制必要 dataclass、helper、连接与查询逻辑
3. 确保该文件在无 `ibkr_readonly.py` 依赖下可独立导入

### P1：补齐交易能力

1. 增加 `ContractSpec` / `OrderRequest` / `ModifyOrderRequest`
2. 增加合约构建与订单构建方法
3. 增加 raw 与高层交易接口
4. 增加订单与成交快照转换逻辑

### P2：测试闭环

1. 新增 `tests/test_ibkr_trading.py`
2. 先写失败测试，再补实现
3. 运行 pytest 验证交易行为与查询回归
4. 确认新文件具备替换旧文件的能力基础

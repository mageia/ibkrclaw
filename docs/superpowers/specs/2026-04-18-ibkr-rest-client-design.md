# IBKR REST Trading Client Design

**Date:** 2026-04-18

**Status:** Approved in chat, pending written-spec review

## Goal

在不影响现有 `scripts/ibkr_trading.py` socket 版的前提下，新增一套基于 IBKR Client Portal Gateway REST API 的交易客户端实现，并提供一份可执行的对比脚本，用于和现有 socket 版并排测试以下能力：账户、余额、持仓、合约查询、行情快照、历史 K 线、scanner、新闻、下单、改单、撤单、订单/成交查询。

## Non-Goals

以下内容不纳入第一版范围：

1. OAuth 直连 `api.ibkr.com`
2. websocket 推送行情/订单流
3. 与 `reqFundamentalData("ReportSnapshot")` 完全等价的基本面能力
4. 替换现有 `scripts/ibkr_trading.py`
5. 隐式 fallback 或 mock 成功路径

## Context

仓库当前主实现为 `scripts/ibkr_trading.py`，基于 `ib_insync + IB Gateway socket API`。仓库内 `references/api-endpoints.md` 保留了历史 Client Portal API 参考，可作为 REST 版第一阶段的接口索引。用户已确认第一版按本地 Client Portal Gateway 方案实现，Base URL 为 `https://localhost:5000/v1/api`。

## Recommended Approach

采用“**新增独立 REST 客户端脚本，接口尽量对齐现有交易脚本**”的方案。

理由：

1. 改动面最小，不破坏现有 socket 版
2. 最容易做功能对比和回归验证
3. 可明确暴露 REST 与 socket 的差异，而不是把差异藏在公共抽象层里

## File Plan

### New Files

1. `scripts/ibkr_rest_trading.py`
   - REST 版主客户端
   - 暴露与 `IBKRTradingClient` 接近的 dataclass、方法名和返回结构
   - 负责 session、REST 请求、响应解析、订单确认链路

2. `tests/test_ibkr_rest_trading.py`
   - REST 客户端单元测试
   - 使用 fake session/fake response 验证请求路径、payload、错误处理、数据映射

3. `scripts/compare_ibkr_clients.py`
   - 并排调用 socket 版与 REST 版
   - 输出差异结果，作为人工/半自动验证入口

### Existing Files To Modify

1. `requirements.txt`
   - 仅在需要时补充依赖；当前预期无需新增，因仓库已包含 `requests`

2. `requirements-dev.txt`
   - 仅在测试依赖需要调整时修改；当前预期无需新增

3. `.github/workflows/python-tests.yml`
   - 需要加入 REST 客户端脚本的编译与测试校验

## Public API Shape

REST 客户端优先复用现有数据模型命名，减少上层调用改造：

- `Position`
- `Quote`
- `FundamentalData`
- `ContractSpec`
- `OrderRequest`
- `ModifyOrderRequest`
- `OrderSnapshot`
- `FillSnapshot`
- `TradeSnapshot`

主类命名为 `IBKRRESTTradingClient`。

### Core Methods

- `connect()`
- `disconnect()`
- `is_authenticated()`
- `get_accounts()`
- `get_balance()`
- `get_positions()`
- `search_symbol()`
- `get_quote()`
- `get_fundamentals()`
- `get_historical_data()`
- `run_scanner()`
- `get_company_news()`
- `place_order()`
- `cancel_order()`
- `modify_order()`
- `get_open_orders()`
- `get_orders()`
- `get_trades()`
- `get_fills()`

`place_order_raw()` / `cancel_order_raw()` / `modify_order_raw()` 在 REST 版第一版中不强求完全复刻 socket 的“原始对象”语义；若保留，也只返回原始 JSON 响应而不伪装成 socket Trade 对象。

## Session Model

REST 版采用 `requests.Session` 持有 cookie，会话管理显式化：

1. `connect()`
   - 调用 `/iserver/auth/status`
   - 必要时调用 brokerage session 初始化/探活接口
   - 调用 `/tickle` 或同类 keepalive 接口验证会话可用
   - 若认证未完成，明确返回失败，不做静默兜底

2. `disconnect()`
   - 调用 `/logout`（若可用）
   - 关闭 session

3. 不实现 socket 风格自动重连事件
   - REST 没有 `disconnectedEvent`
   - 失败通过异常/False/日志显式暴露

## Endpoint Mapping

### Accounts / Portfolio

- `GET /portfolio/accounts` -> `get_accounts()`
- `GET /portfolio/{accountId}/summary` + `GET /portfolio/{accountId}/ledger` -> `get_balance()`
- `GET /portfolio/{accountId}/positions/{pageId}` -> `get_positions()`

### Contract Discovery

- `GET /iserver/secdef/search` -> 股票/通用 symbol search
- 期货、期权相关扩展在第一版仅做“有则支持”的最小实现，不追求 socket 版 `qualifyContracts()` 全覆盖

### Market Data

- `GET /iserver/marketdata/snapshot` -> `get_quote()`
- `GET /iserver/marketdata/history` -> `get_historical_data()`

### Scanner

- `GET /iserver/scanner/params`
- `POST /iserver/scanner/run`

### Orders

- `POST /iserver/account/{accountId}/orders` -> `place_order()`
- `POST /iserver/reply/{replyId}` -> 下单确认链路
- `DELETE /iserver/account/{accountId}/order/{orderId}` -> `cancel_order()`
- `POST /iserver/account/{accountId}/order/{orderId}` -> `modify_order()`
- `GET /iserver/account/orders` -> `get_open_orders()` / `get_orders()`
- `GET /iserver/account/trades` -> `get_trades()` / `get_fills()`

### News

第一版继续沿用外部 RSS 聚合模式，与现有 `scripts/ibkr_trading.py` 保持一致，不强制迁移到 IB 新闻接口。

## Data Mapping Rules

### Balance

输出结构沿用当前脚本：

```python
Dict[str, List[Dict[str, Any]]]
```

并保留：

- `amount`
- `currency`
- `account`

### Position

优先映射以下字段：

- `symbol`
- `conid`
- `quantity`
- `avg_cost`
- `market_value`
- `unrealized_pnl`
- `pnl_percent`

若 REST 响应没有完整成本基础，则使用可得字段计算，计算失败时显式保留为 `0`，不伪造精确值。

### Quote

使用 REST snapshot 字段映射：

- `31` -> last price
- `84` -> bid
- `86` -> ask
- `87` -> volume
- `88` -> previous close
- `7762` -> change %（若可得）

若 `change` 不能直接取到，则按 `last - close` 计算。

### Fundamentals

REST 第一版定义为“**部分字段可得版本**”。

目标字段：

- `company_name`
- `industry`
- `category`
- `market_cap`
- `pe_ratio`
- `eps`
- `dividend_yield`
- `high_52w`
- `low_52w`
- `avg_volume`

约束：

- 如果 REST 文档/响应未提供字段，则返回 `"N/A"`
- 不通过猜测或外部站点补齐 `market_cap` / `pe_ratio` / `eps`
- 52 周高低点和均量可优先从 market data 可得字段中抽取

## Order Model Rules

REST 版订单请求保持现有 `OrderRequest` / `ModifyOrderRequest` 结构，但会做显式映射：

- `MKT` -> `MKT`
- `LMT` -> `LMT`
- `STP` -> `STP`
- `STP_LMT` -> `STOP_LIMIT`（若网关要求该命名）

字段映射：

- `action` -> `side`
- `quantity` -> `quantity`
- `limit_price` -> `price`
- `stop_price` -> `auxPrice`
- `tif` -> `tif`
- `outside_rth` -> `outsideRTH`（若接口支持）

第一版只承诺稳定支持：

- 美股股票 `STK`
- `MKT` / `LMT` / `STP`
- `STP_LMT` 仅在测试确认网关接受时开放；若实测不兼容则显式报错

## Order Confirmation Flow

REST 下单与改单必须显式处理 confirmation reply：

1. 发送订单请求
2. 若响应为最终订单结果，则直接标准化为 `TradeSnapshot`
3. 若响应含 `replyId` / question，需要再次调用 `/iserver/reply/{replyId}`
4. 不自动吞掉 warning；将 warning 内容保留在异常或日志中

## Error Handling

遵守仓库“Debug-First / No Silent Fallbacks”原则：

1. 不新增静默 fallback
2. HTTP 非 2xx 直接抛出或返回明确错误
3. JSON 结构异常直接暴露上下文
4. 会话未认证直接失败，不偷偷重试成别的路径
5. 文档确认不支持的字段，明确返回 `N/A` 而不是伪造值

## Testing Strategy

### Unit Tests

`tests/test_ibkr_rest_trading.py` 将覆盖：

1. `connect()` 的认证状态处理
2. `get_accounts()` / `get_balance()` / `get_positions()` 的 JSON 映射
3. `search_symbol()` / `get_quote()` / `get_historical_data()` 的 endpoint 与字段映射
4. `run_scanner()`
5. `place_order()` 的 payload 与 confirmation 链路
6. `cancel_order()` / `modify_order()`
7. `get_orders()` / `get_trades()` / `get_fills()`
8. `get_fundamentals()` 在字段缺失时返回 `N/A`

### Local Comparison Script

`scripts/compare_ibkr_clients.py` 将在本地真实环境执行以下对比：

1. `get_balance()`
2. `get_positions()`
3. `get_quote("AAPL")`
4. `get_historical_data("AAPL")`
5. `run_scanner()`
6. `get_orders()` / `get_trades()`
7. 可选：在 paper 账户中执行最小订单测试

对比输出目标：

- 每项功能是否成功
- REST 与 socket 返回结构是否一致
- 关键数值是否存在明显偏差
- 哪些字段是 REST 版缺失或降级的

## Risks

1. 同一用户名 brokerage session 可能与现有 socket 会话冲突
2. `fundamentals` 无法与 socket 版完全等价
3. `STP_LMT` 的 REST 命名与字段要求需实测确认
4. `/iserver/account/trades`、订单确认响应的 JSON 结构可能与历史参考文档不同

## Success Criteria

第一版完成后，满足以下条件即视为成功：

1. 新增 REST 客户端脚本可通过单元测试
2. CI 覆盖 REST 脚本编译与测试
3. 在本地 CP Gateway 环境下，可跑通至少以下真实调用：
   - accounts
   - balance
   - positions
   - quote
   - historical data
   - scanner
   - orders query
4. paper 环境下至少完成一次最小下单/撤单或改单验证
5. 生成一份 socket vs REST 的差异输出

## Implementation Notes

实施阶段应优先保证：

1. 接口形状稳定
2. 错误可见
3. 测试先行
4. 不破坏现有 `scripts/ibkr_trading.py`

若后续需要长期维护，再考虑抽共享模型层，而不是在第一版提前抽象。

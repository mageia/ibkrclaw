---
name: ibkr-trading
description: Use when querying IBKR accounts, researching securities, or preparing to place, cancel, or modify IBKR orders through scripts/ibkr_trading.py with confirmation-before-execution.
---

# IBKR 交易技能

⚠️ **交易执行规则**：查询请求可直接执行；任何下单、撤单、改单请求都必须 **先确认再执行**。

## 架构

通过 **IB Gateway**（桌面版）+ **ib_insync**（socket API）直连，替代了旧的 HTTP Gateway 方案。

| 组件 | 说明 |
|------|------|
| `scripts/ibkr_trading.py` | 主交易客户端，支持查询、下单、撤单、改单、订单/成交查询 |
| `keepalive.py` | 健康检查脚本，断线时发 Telegram 通知 |
| IB Gateway | IBKR 官方桌面应用，常驻后台，支持 Auto Restart |
| ib_insync | Python socket API 客户端，负责连接、合约、订单与回报 |

## 功能

| 功能 | 说明 |
|------|------|
| ✅ 查看持仓 | 显示股票/期权/期货持仓、成本、市值、盈亏 |
| ✅ 查看余额 | 显示现金余额、净资产、账户汇总 |
| ✅ 实时行情 | 查询任意标的的价格、买卖盘、成交量 |
| ✅ 深度基本面 | 查询市值、P/E、EPS、股息收益、行业分类 |
| ✅ 历史 K 线 | 获取过去 N 天/月/年的价格序列，用于趋势分析 |
| ✅ 市场扫描 | 查询全市场涨幅榜、跌幅榜及异动榜 |
| ✅ 新闻检索 | 获取 Yahoo Finance RSS 新闻并结合全网信息做分析 |
| ✅ 下单 | 支持股票 / ETF / 期权 / 期货下单 |
| ✅ 修改订单 | 支持按 order id 调整数量、限价、止损价等 |
| ✅ 取消订单 | 支持按 order id 撤单 |
| ✅ 订单 / 成交查询 | 支持查询未完成订单、订单列表、trade、fill |

## 🤖 AI 助理执业规范 (Agent Execution Protocol)

### A. 查询 / 投研模式

当用户是在做研究、复盘或持仓分析时，执行以下流程：

1. **提取核心数据**
   - 优先使用仓库内的 `scripts/ibkr_trading.py`；若已部署到生产目录，则使用 `~/trading/ibkr_trading.py`。
   - 通过 `IBKRTradingClient` 获取持仓、余额、行情、基本面、历史数据、扫描器结果与新闻数据。
2. **强制全网深度检索**
   - 不能只依赖 RSS 新闻。必须使用 web search 检索最新宏观事件、财报会议、行业竞争、产品动态与监管变化。
3. **推演与逻辑链**
   - 分析外部变量如何影响盈利预期、估值、市场情绪和股价表现，不要只堆砌新闻。
4. **输出高管级研报**
   - 默认按以下结构输出：
     - `1. 📊 盘面与基本面速览`
     - `2. 🌪️ 核心事件驱动`
     - `3. 🧠 竞品与护城河分析`
     - `4. 💡 总结与投资视角`

### B. 交易执行模式

当用户明确提出“买入 / 卖出 / 撤单 / 改单 / 查订单 / 查成交”时，执行以下流程：

1. **识别交易意图**
   - 提取标的、方向、数量、订单类型、限价/止损价、TIF、是否盘前盘后、order id 等参数。
2. **参数归一化**
   - 使用 `scripts/ibkr_trading.py` 里的 `ContractSpec`、`OrderRequest`、`ModifyOrderRequest` 组织参数。
3. **先确认再执行**
   - 在真正提交任何交易操作前，必须先回显：
     - 标的 / 合约类型
     - 买卖方向
     - 数量
     - 订单类型
     - 限价 / 止损价（如有）
     - TIF / outsideRth / account（如有）
     - 当前连接环境（由 `IB_HOST` / `IB_PORT` / `IB_CLIENT_ID` 决定）
   - 只有在用户明确确认后，才允许调用下单、撤单或改单逻辑。
4. **执行并回报结果**
   - 提交后返回标准化结果，包括 `order_id`、状态、数量、价格、剩余数量、回报信息。
5. **失败必须显式暴露**
   - 合约无法 qualify、订单找不到、参数不合法、API 返回异常时，必须明确报错，不允许静默伪成功。

## 前置条件

1. IBKR 账户（模拟盘或实盘）
2. 手机安装 IBKR Key App（首次登录 IB Gateway 需要 2FA）
3. Mac 需要 Java 17+ 和 Python 3.9+
4. **IB Gateway** 桌面应用（从 IBKR 官网下载）
5. **强烈建议**先在 paper 环境验证交易流程，再连接真实账户

## 快速配置

### 1. 安装依赖

```bash
# 安装 Java
brew install openjdk@17

# 在仓库目录运行安装脚本
cd ibkrclaw
bash scripts/setup.sh ~/trading

# 同步交易主脚本到部署目录
install -m 644 scripts/ibkr_trading.py ~/trading/ibkr_trading.py
```

### 2. 安装 IB Gateway

从 IBKR 官网下载 **IB Gateway**（Stable channel）：
https://www.interactivebrokers.com/en/trading/ibgateway-stable.php

### 3. 配置 IB Gateway API Settings

在 IB Gateway 界面中：
- ✅ **Enable ActiveX and Socket Clients**
- ❌ **Read-Only API**（不要勾选，否则会阻止交易与部分查询能力）
- Socket port：按你的部署环境设置（实盘常见 **4001**，模拟盘常见 **4002**）
- Trusted IPs：**127.0.0.1**
- ✅ **Auto Restart**（Settings → Lock and Exit → Auto restart）

### 4. 配置环境变量

`~/trading/.env`：
```bash
IB_HOST=127.0.0.1
IB_PORT=4001
IB_CLIENT_ID=1
TG_BOT_TOKEN=
TG_CHAT_ID=
TG_NOTIFY_COOLDOWN=900
```

> 默认沿用环境变量 `IB_HOST` / `IB_PORT` / `IB_CLIENT_ID`，由部署环境决定连接的是模拟盘还是实盘。

### 5. 测试连接

```bash
cd ~/trading && source venv/bin/activate
python - <<'PY'
from ibkr_trading import IBKRTradingClient

client = IBKRTradingClient()
print("connect:", client.connect())
print("accounts:", client.get_accounts())
client.disconnect()
PY
```

## 使用方法

### 在 Python 中调用

```python
from ibkr_trading import IBKRTradingClient, ContractSpec, OrderRequest

client = IBKRTradingClient()
client.connect()

positions = client.get_positions()
orders = client.get_open_orders()

request = OrderRequest(
    contract=ContractSpec(sec_type="STK", symbol="AAPL"),
    action="BUY",
    quantity=10,
    order_type="LMT",
    limit_price=180.0,
)
# 真实执行前，agent 必须先确认再执行
```

### 在 OpenClaw / Telegram 中使用

直接对机器人说：
- 查询类：
  - “我的 IBKR 持仓有哪些？”
  - “帮我查一下账户余额和当前未完成订单”
  - “帮我看看 AAPL 最近的基本面和新闻”
  - “利用 IBKR 历史数据分析一下 NVDA 最近 3 个月走势”
- 交易类：
  - “帮我买入 10 股 AAPL，限价 180”
  - “帮我卖出 2 张 TSLA 期权，市价单”
  - “撤掉 order id 12345”
  - “把 order id 12345 改成 20 股，限价 179.5”
  - “帮我查一下今天的成交回报”

## 健康检查

通过 `keepalive.py` 每 5 分钟检查 IB Gateway 状态，断线时发 Telegram 通知：

```bash
*/5 * * * * cd ~/trading && source venv/bin/activate && python keepalive.py >> ~/trading/keepalive.log 2>&1
```

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| 连接失败 | 检查 IB Gateway 是否启动并登录，确认 `IB_HOST` / `IB_PORT` / `IB_CLIENT_ID` 与部署环境一致 |
| 端口不通 | 检查 API Settings 中 Socket port、Trusted IP、Socket Clients |
| 交易请求未执行 | 检查是否还停留在“先确认再执行”阶段，确认用户是否已明确确认 |
| 撤单/改单失败 | 检查 order id 是否存在，以及目标订单是否仍处于可操作状态 |
| 认证过期 | 重启 IB Gateway 并重新登录，确认 Auto Restart 设置正常 |

## 安全说明

此技能**支持交易**，但执行规范必须满足：
- 所有交易请求一律 **先确认再执行**
- 默认连接目标由 `IB_HOST` / `IB_PORT` / `IB_CLIENT_ID` 决定，不在技能里硬编码 live/paper
- 强烈建议先在 paper 环境验证策略、参数和流程
- 如果用户请求参数不完整或存在歧义，必须先澄清，不能猜测后直接下单

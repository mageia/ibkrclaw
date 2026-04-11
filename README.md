# IBKR 交易 Skill for OpenClaw

> ⚠️ **交易执行规则**：查询可直接执行；任何买入、卖出、撤单、改单请求都必须 **先确认再执行**。

通过 [OpenClaw](https://openclaw.ai) 在 Telegram 中直接完成你的 IBKR 查询与交易执行，包括：持仓、余额、行情、基本面、历史数据、扫描器、下单、撤单、改单以及订单/成交查询。

---

### 🔥 实际效果演示

![IBKR 持仓查询演示](1.png)

![IBKR 深度投研分析演示](2.png)

---

## 📢 v2.0 架构升级：IB Gateway + ib_insync

> **2026-02-27 重大更新**

### 为什么升级？

v1.0 使用 **Client Portal Gateway**（HTTP REST API）+ 自动登录方案，在稳定性和维护成本上都很差。v2.0 改为：

- **IB Gateway** 负责稳定会话
- **ib_insync** 负责 socket 直连
- **keepalive.py** 负责健康检查与 Telegram 通知
- **ibkr_trading.py** 负责查询与交易能力的统一入口

### v2.0 当前能力

| 能力 | 说明 |
|------|------|
| 查询 | 余额、持仓、行情、基本面、历史数据、scanner、新闻 |
| 交易 | 下单、撤单、改单、订单查询、成交查询 |
| 连接 | IB Gateway socket 直连，自动重连 |
| 环境 | 默认沿用 `IB_HOST` / `IB_PORT` / `IB_CLIENT_ID`，由部署环境决定 live / paper |

---

## ⚡ 一键安装（推荐）

直接把以下内容发送给你的 OpenClaw 机器人：

```
请帮我安装这个 Skill：https://github.com/liusai0820/ibkrclaw.git

安装完成后，请帮我配置 IB Gateway + ib_insync 环境，并启用交易版入口。
```

---

## 📋 前置条件

| 条件 | 说明 |
|------|------|
| **强烈建议先用模拟盘** | 请优先在 paper 环境验证下单、撤单、改单流程，确认无误后再切换实盘 |
| IBKR 账户 | 可以是模拟盘或实盘 |
| IBKR Key App | 首次登录 IB Gateway 需要手机 2FA |
| Java 17+ | `brew install openjdk@17` |
| Python 3.9+ | 用于运行 `ibkr_trading.py` 与健康检查脚本 |
| IB Gateway | 从 IBKR 官网下载桌面应用 |

---

## 🛠️ 安装步骤

### 第 1 步：安装依赖

```bash
# 安装 Java（如已安装可跳过）
brew install openjdk@17

# 在仓库目录执行安装脚本
cd ibkrclaw
bash scripts/setup.sh ~/trading

# 同步交易主脚本到部署目录
install -m 644 scripts/ibkr_trading.py ~/trading/ibkr_trading.py
```

> 当前 `setup.sh` 负责初始化运行环境与 keepalive；交易主入口请额外同步 `ibkr_trading.py` 到部署目录。

### 第 2 步：安装 IB Gateway

从 IBKR 官网下载 **IB Gateway Stable**：
https://www.interactivebrokers.com/en/trading/ibgateway-stable.php

下载后安装到 Applications，并完成首次登录。

### 第 3 步：配置 API Settings

登录后进入 **Configure → Settings → API**：

| 设置项 | 值 |
|--------|-----|
| Enable ActiveX and Socket Clients | ✅ |
| Read-Only API | ❌ **不要勾选** |
| Socket port | 按部署环境填写（实盘常见 4001，模拟盘常见 4002） |
| Trusted IPs | 127.0.0.1 |

然后进入 **Configure → Settings → Lock and Exit**：

| 设置项 | 值 |
|--------|-----|
| Auto Restart | ✅ 勾选 |

### 第 4 步：配置环境变量

创建 `~/trading/.env`：

```bash
IB_HOST=127.0.0.1
IB_PORT=4001
IB_CLIENT_ID=1
TG_BOT_TOKEN=
TG_CHAT_ID=
TG_NOTIFY_COOLDOWN=900
```

> 默认沿用环境变量 `IB_HOST` / `IB_PORT` / `IB_CLIENT_ID`，由部署环境决定连接到模拟盘还是实盘。

### 第 5 步：测试连接

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

---

## 💬 在 OpenClaw / Telegram 中使用

### 查询类请求

| 你说的话 | 机器人返回 |
|----------|-----------|
| 我的 IBKR 持仓有哪些？ | 所有持仓、成本价、当前市值、盈亏 |
| 帮我查一下账户余额和当前未完成订单 | 账户余额 + 未完成订单列表 |
| 帮我看看苹果 (AAPL) 最近的基本面，市值和市盈率怎么样？ | 最新基本面数据 + 财报与业务分析 |
| 利用 IBKR 历史数据，分析一下 NVDA 最近 3 个月的走势 | 调用历史 K 线并输出趋势判断 |
| 今天美股涨得最猛的 10 只股票是哪些？ | 调取市场扫描器获取涨幅榜，并分析哪些板块领涨 |
| 帮我查一下 LMND 最近有什么新闻，为什么暴跌？ | 聚合新闻 + AI 事件驱动分析 |

### 交易类请求

| 你说的话 | 系统行为 |
|----------|-----------|
| 帮我买入 10 股 AAPL，限价 180 | **先确认再执行**，回显订单参数后等待确认 |
| 帮我卖出 2 张 TSLA 期权，市价单 | **先确认再执行** |
| 撤掉 order id 12345 | **先确认再执行** |
| 把 order id 12345 改成 20 股，限价 179.5 | **先确认再执行** |
| 帮我查一下今天的成交回报 | 返回 fills / trades 信息 |

---

## 🔄 稳定性保障

### IB Gateway Auto Restart

IB Gateway 自带 **Auto Restart**，可将会话维持在一周级别，明显优于旧方案的 24 小时过期模型。

### ib_insync 自动重连

`IBKRTradingClient` 内置断线重连逻辑，网络短暂中断后会尝试自动恢复连接，并重新设置 market data type。

### keepalive.py 健康检查

通过 `keepalive.py`（cron 每 5 分钟执行），监控 Gateway 进程和端口状态，异常时发送 Telegram 通知：

```bash
*/5 * * * * cd ~/trading && source venv/bin/activate && python keepalive.py >> ~/trading/keepalive.log 2>&1
```

---

## 🔧 功能说明

| 功能 | 支持 | 说明 |
|------|------|------|
| 查看持仓 | ✅ | 股票 / 期权 / 期货持仓、成本价、市值、盈亏 |
| 查看余额 | ✅ | 现金余额、净资产、账户汇总 |
| 实时行情 | ✅ | 任意标的的价格、买卖盘、成交量 |
| 深度基本面 | ✅ | 公司市值、P/E、EPS、股息收益、行业分类 |
| 历史 K 线走势 | ✅ | 任意时间跨度的 OHLCV 数据 |
| 市场大盘扫描 | ✅ | 涨幅榜、跌幅榜、成交量异动榜 |
| 最新财经事件 | ✅ | Yahoo Finance RSS 新闻聚合 + AI 分析 |
| 下单 | ✅ | 通过 `OrderRequest` / raw order 执行 |
| 修改订单 | ✅ | 通过 `ModifyOrderRequest` / raw trade 执行 |
| 取消订单 | ✅ | 通过 order id 或 raw order/trade 执行 |
| 订单 / 成交查询 | ✅ | 支持 open orders、orders、trades、fills |

---

## 📁 文件结构

```
ibkr-trader/
├── SKILL.md              # OpenClaw Skill 描述文件
├── README.md             # 本文档
├── scripts/
│   ├── setup.sh          # 安装脚本（初始化 Python 环境）
│   ├── ibkr_trading.py   # 核心交易与查询客户端（ib_insync 版）
│   └── keepalive.py      # 健康检查脚本（进程/端口监控 + Telegram 通知）
└── references/
    └── ...               # 参考文档
```

---

## 安全说明

本项目**支持交易执行**，但遵守以下原则：
- 所有交易请求一律 **先确认再执行**
- 如果参数不完整或存在歧义，必须先澄清，不能直接下单
- 默认连接目标由 `IB_HOST` / `IB_PORT` / `IB_CLIENT_ID` 决定
- 强烈建议先在 paper 环境完成验证，再切换到真实账户

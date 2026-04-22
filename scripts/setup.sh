#!/bin/bash
# IBKR Readonly v2 Setup Script

set -euo pipefail

TRADING_DIR="${1:-$HOME/trading}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$TRADING_DIR/.env"

print_step() {
    echo ""
    echo "[$1] $2"
}

echo "🏦 IBKR Readonly v2 Setup"
echo "=========================="
echo "Install dir: $TRADING_DIR"

print_step "1/5" "检查运行时依赖"
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 未找到 python3，请先安装 Python 3.9+"
    exit 1
fi

if ! command -v java >/dev/null 2>&1; then
    echo "❌ 未找到 Java，请先安装 Java 17+"
    exit 1
fi

echo "✅ python3: $(python3 --version)"
echo "✅ java: $(java -version 2>&1 | head -1)"

print_step "2/5" "创建部署目录与虚拟环境"
mkdir -p "$TRADING_DIR"
cd "$TRADING_DIR"

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ 已创建 venv"
else
    echo "✅ 复用现有 venv"
fi

# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install ib_insync requests

echo "✅ 已安装 Python 依赖: ib_insync requests"

print_step "3/5" "复制运行脚本"
install -m 644 "$REPO_ROOT/scripts/ibkr_readonly.py" "$TRADING_DIR/ibkr_readonly.py"
install -m 644 "$REPO_ROOT/scripts/keepalive.py" "$TRADING_DIR/keepalive.py"
echo "✅ 已复制 ibkr_readonly.py 与 keepalive.py"

print_step "4/5" "生成 .env 模板"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'ENVEOF'
IB_HOST=127.0.0.1
IB_PORT=4002
IB_CLIENT_ID=1
TG_BOT_TOKEN=
TG_CHAT_ID=
TG_NOTIFY_COOLDOWN=900
ENVEOF
    echo "✅ 已创建 .env"
else
    echo "✅ 检测到已存在 .env，未覆盖"
fi

print_step "5/5" "后续操作提示"
echo "1) 启动并登录 IB Gateway（Socket port=4002 Trusted IP=127.0.0.1）"
echo "2) 编辑 $ENV_FILE，按需填写 TG_BOT_TOKEN / TG_CHAT_ID"
echo "3) 测试连接：cd $TRADING_DIR && source venv/bin/activate && python ibkr_readonly.py"
echo "4) 可选保活：cd $TRADING_DIR && source venv/bin/activate && python keepalive.py"

echo ""
echo "✅ Readonly v2 环境初始化完成"

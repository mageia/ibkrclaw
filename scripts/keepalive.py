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

# IB Gateway 配置
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4002"))

# Telegram 通知配置（可选）
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

# 状态文件，避免重复通知
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gw_state")


def log(msg):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{timestamp} {msg}")


def check_gateway_process() -> bool:
    """检查 IB Gateway 进程是否存在"""
    try:
        # IB Gateway 在 macOS 上以 JavaApplicationStub 运行，路径含 "IB Gateway"
        for pattern in [
            "ibgateway",
            "IB Gateway",
            "IBGateway",
            "Trader Workstation",
            "Trader",
        ]:
            result = subprocess.run(
                ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return True
        return False
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


def send_telegram(message: str):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        import requests

        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"},
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


def main():
    process_ok = check_gateway_process()
    socket_ok = check_socket_connection()
    last_state = read_state()

    if process_ok and socket_ok:
        # 一切正常
        if last_state != "ok":
            log("✅ IB Gateway 恢复正常")
            send_telegram("✅ IB Gateway 已恢复连接！Agent 后台数据通道恢复。")
        else:
            log("✅ IB Gateway running - port reachable")
        write_state("ok")

    elif process_ok and not socket_ok:
        # 进程在但端口不通（可能正在启动或登录中）
        log("⚠️ IB Gateway 进程在运行，但端口不通（可能需要登录）")
        if last_state != "port_down":
            send_telegram(
                "⚠️ <b>IB Gateway 端口不通</b>\n"
                f"进程在运行，但 {IB_HOST}:{IB_PORT} 无法连接。\n"
                "可能原因：未登录 / 正在启动中\n"
                "请检查 IB Gateway 登录状态。"
            )
        write_state("port_down")

    else:
        # 进程都没跑
        log("❌ IB Gateway 进程未运行")
        if last_state != "down":
            send_telegram(
                "❌ <b>IB Gateway 已停止</b>\n"
                "进程未运行，所有实盘数据查询不可用。\n"
                "请在 Mac mini 上重新启动 IB Gateway 并登录。"
            )
        write_state("down")


if __name__ == "__main__":
    main()

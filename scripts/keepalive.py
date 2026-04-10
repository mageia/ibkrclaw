#!/usr/bin/env python3
"""
IB Gateway 健康检查脚本
每 5 分钟由 cron 执行，检查 IB Gateway 连接状态。
断线时发送 Telegram 通知。

Crontab entry:
*/5 * * * * cd ~/trading && venv/bin/python keepalive.py >> ~/trading/keepalive.log 2>&1
"""

import os
import sys
import socket
import subprocess
from importlib import util
from datetime import datetime
from pathlib import Path

# IB Gateway 配置
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4001"))

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
        result = subprocess.run(
            ["pgrep", "-f", "ibgateway"],
            capture_output=True, text=True, timeout=5
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
    """构建只读 IBKR 客户端"""
    script_path = Path(__file__).resolve().parent / "ibkr_readonly.py"
    spec = util.spec_from_file_location("ibkr_readonly", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {script_path}")

    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.IBKRReadOnlyClient(host=IB_HOST, port=IB_PORT)


def check_api_readiness(client_factory=build_readonly_client) -> bool:
    """检查 API 可用性：连接并执行一次轻量查询"""
    client = None
    try:
        client = client_factory()
        connected = client.connect()
        if connected is False:
            raise RuntimeError("connect() returned False")
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
            f"Socket 端口 {IB_HOST}:{IB_PORT} 可连，但 API 不可用。\n"
            "请检查 IB Gateway API 设置与会话状态。"
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

    if state == "down":
        send_telegram(
            "❌ <b>IB Gateway 已停止</b>\n"
            "进程未运行，所有实盘数据查询不可用。\n"
            "请在 Mac mini 上重新启动 IB Gateway 并登录。"
        )


def send_telegram(message: str):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
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
    socket_ok = check_socket_connection() if process_ok else False
    api_ok = check_api_readiness() if process_ok and socket_ok else False
    last_state = read_state()
    current_state = evaluate_gateway_status(process_ok, socket_ok, api_ok)

    if current_state == "ok":
        if last_state != "ok":
            log("✅ IB Gateway 恢复正常")
            send_transition_notification("ok")
        else:
            log("✅ IB Gateway running - port reachable")
        write_state(current_state)

    elif current_state == "api_down":
        log("⚠️ IB Gateway 进程和端口正常，但 API 不可用")
        if last_state != "api_down":
            send_transition_notification("api_down")
        write_state(current_state)

    elif current_state == "port_down":
        log("⚠️ IB Gateway 进程在运行，但端口不通（可能需要登录）")
        if last_state != "port_down":
            send_transition_notification("port_down")
        write_state(current_state)

    else:
        log("❌ IB Gateway 进程未运行")
        if last_state != "down":
            send_transition_notification("down")
        write_state(current_state)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compare socket client and REST client outputs."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from typing import Any


def _normalize_payload(payload: Any) -> Any:
    if is_dataclass(payload):
        return _normalize_payload(asdict(payload))
    if isinstance(payload, dict):
        return {key: _normalize_payload(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_normalize_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [_normalize_payload(item) for item in payload]
    return payload


def _compare_section(socket_value: Any, rest_value: Any) -> dict[str, Any]:
    normalized_socket = _normalize_payload(socket_value)
    normalized_rest = _normalize_payload(rest_value)
    return {
        "match": normalized_socket == normalized_rest,
        "socket": normalized_socket,
        "rest": normalized_rest,
    }


def compare_clients(socket_client: Any, rest_client: Any, *, symbol: str) -> dict[str, dict[str, Any]]:
    return {
        "balance": _compare_section(socket_client.get_balance(), rest_client.get_balance()),
        "positions": _compare_section(socket_client.get_positions(), rest_client.get_positions()),
        "quote": _compare_section(socket_client.get_quote(symbol), rest_client.get_quote(symbol)),
    }


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare IBKR socket and REST clients")
    parser.add_argument("--symbol", required=True, help="Ticker symbol to compare")
    return parser


def main() -> int:
    args = _build_argument_parser().parse_args()

    try:
        from scripts.ibkr_rest_trading import IBKRRESTTradingClient
        from scripts.ibkr_trading import IBKRTradingClient
    except ImportError:
        from ibkr_rest_trading import IBKRRESTTradingClient
        from ibkr_trading import IBKRTradingClient

    socket_client = IBKRTradingClient()
    rest_client = IBKRRESTTradingClient()

    try:
        if not socket_client.connect():
            raise RuntimeError("socket client connect() returned False")
        if not rest_client.connect():
            raise RuntimeError("rest client connect() returned False")

        comparison = compare_clients(socket_client, rest_client, symbol=args.symbol)
        print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        socket_client.disconnect()
        rest_client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())

from dataclasses import FrozenInstanceError
from importlib import util
from pathlib import Path
import sys

import pytest


def _load_ibkr_rest_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ibkr_rest_trading.py"
    spec = util.spec_from_file_location("ibkr_rest_trading", script_path)
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


ibkr_rest_module = _load_ibkr_rest_module()


class FakeSession:
    def __init__(self):
        self.closed = False

    def request(self, *args, **kwargs):
        raise AssertionError("request should not be called")

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, status_code, text, payload):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class CapturingSession:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    def close(self):
        self.closed = True


def test_load_module_does_not_leave_sys_modules_entry():
    assert "ibkr_rest_trading" not in sys.modules


def test_rest_client_uses_injected_session_factory():
    fake_session = FakeSession()
    client = ibkr_rest_module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api",
        default_account_id="DU123",
        session_factory=lambda: fake_session,
    )

    assert client.base_url == "https://localhost:5000/v1/api"
    assert client.default_account_id == "DU123"
    assert client.session is fake_session


def test_request_json_assembles_request_and_returns_json_payload():
    response = FakeResponse(200, "ok", {"result": "success"})
    session = CapturingSession(response)
    client = ibkr_rest_module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api/",
        timeout_seconds=42.5,
        verify_ssl=True,
        session_factory=lambda: session,
    )

    payload = client._request_json(
        "get",
        "/portfolio/accounts",
        params={"currency": "USD"},
        payload={"includeClosed": False},
    )

    assert payload == {"result": "success"}
    assert session.calls == [
        (
            "GET",
            "https://localhost:5000/v1/api/portfolio/accounts",
            {
                "params": {"currency": "USD"},
                "json": {"includeClosed": False},
                "timeout": 42.5,
                "verify": True,
            },
        )
    ]


def test_request_json_raises_with_method_path_and_status():
    class ErrorSession:
        def request(self, *args, **kwargs):
            return FakeResponse(503, "gateway unavailable", {"ok": False})

        def close(self):
            return None

    client = ibkr_rest_module.IBKRRESTTradingClient(
        base_url="https://localhost:5000/v1/api",
        session_factory=lambda: ErrorSession(),
    )

    with pytest.raises(RuntimeError) as exc_info:
        client._request_json("post", "/iserver/account/orders")

    message = str(exc_info.value)
    assert "POST /iserver/account/orders failed" in message
    assert "503" in message
    assert "gateway unavailable" in message


def test_position_is_immutable():
    position = ibkr_rest_module.Position(
        symbol="AAPL",
        conid=1,
        quantity=1.0,
        avg_cost=100.0,
        market_value=101.0,
        unrealized_pnl=1.0,
        pnl_percent=1.0,
    )

    with pytest.raises(FrozenInstanceError):
        position.quantity = 2.0


def test_disconnect_closes_injected_session():
    session = FakeSession()
    client = ibkr_rest_module.IBKRRESTTradingClient(session_factory=lambda: session)

    client.disconnect()

    assert session.closed is True


def test_default_timeout_is_float():
    assert isinstance(ibkr_rest_module.DEFAULT_TIMEOUT_SECONDS, float)

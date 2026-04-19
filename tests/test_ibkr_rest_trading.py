from importlib import util
from pathlib import Path
import sys

import pytest



def _load_ibkr_rest_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ibkr_rest_trading.py"
    spec = util.spec_from_file_location("ibkr_rest_trading", script_path)
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
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

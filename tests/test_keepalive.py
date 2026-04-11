import os
import time
from importlib import util
from pathlib import Path
from types import SimpleNamespace


os.environ["IB_HOST"] = "127.0.0.1"
os.environ["IB_PORT"] = "4001"


def _load_keepalive_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "keepalive.py"
    spec = util.spec_from_file_location("keepalive", script_path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


keepalive = _load_keepalive_module()


def test_evaluate_gateway_status_distinguishes_api_down():
    assert keepalive.evaluate_gateway_status(False, False, False) == "down"
    assert keepalive.evaluate_gateway_status(True, False, False) == "port_down"
    assert keepalive.evaluate_gateway_status(True, True, False) == "api_down"
    assert keepalive.evaluate_gateway_status(True, True, True) == "ok"


def test_check_api_readiness_returns_true_and_disconnects_client():
    class FakeClient:
        def __init__(self):
            self.disconnect_called = False

        def connect(self):
            return True

        def get_accounts(self):
            return ["DU123456"]

        def disconnect(self):
            self.disconnect_called = True

        def isConnected(self):
            return True

    client = FakeClient()

    def factory():
        return client

    result = keepalive.check_api_readiness(factory)

    assert result is True
    assert client.disconnect_called


def test_check_api_readiness_returns_false_when_query_fails(capfd):
    class FailingClient:
        def connect(self):
            raise RuntimeError("gateway timeout")

        def disconnect(self):
            pass

        def isConnected(self):
            return False

    def factory():
        return FailingClient()

    result = keepalive.check_api_readiness(factory)

    assert result is False
    captured = capfd.readouterr()

    assert "API readiness check failed" in captured.out
    assert "gateway timeout" in captured.out


def test_main_sends_notification_when_state_changes_to_api_down(monkeypatch):
    sent_messages = []
    recorded_states = []

    monkeypatch.setattr(keepalive, "read_state", lambda: "ok")
    monkeypatch.setattr(keepalive, "write_state", lambda state: recorded_states.append(state))
    monkeypatch.setattr(keepalive, "send_telegram", lambda message: sent_messages.append(message))
    monkeypatch.setattr(keepalive, "check_gateway_process", lambda: True)
    monkeypatch.setattr(keepalive, "check_socket_connection", lambda: True)
    monkeypatch.setattr(keepalive, "check_api_readiness", lambda factory=None: False)

    keepalive.main()

    assert recorded_states
    assert recorded_states[-1] == "api_down"
    assert any("API 不可用" in message for message in sent_messages)


def test_build_readonly_client_uses_keepalive_client_id(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, host, port, client_id):
            captured["host"] = host
            captured["port"] = port
            captured["client_id"] = client_id

    class FakeLoader:
        def exec_module(self, module):
            module.IBKRReadOnlyClient = FakeClient

    fake_spec = SimpleNamespace(loader=FakeLoader())
    monkeypatch.setattr(keepalive.util, "spec_from_file_location", lambda *args, **kwargs: fake_spec)
    monkeypatch.setattr(keepalive.util, "module_from_spec", lambda spec: SimpleNamespace())

    client = keepalive.build_readonly_client()

    assert isinstance(client, FakeClient)
    assert captured["host"] == keepalive.IB_HOST
    assert captured["port"] == keepalive.IB_PORT
    assert captured["client_id"] == keepalive.KEEPALIVE_CLIENT_ID


def test_check_api_readiness_uses_runtime_default_factory(monkeypatch):
    called = {"value": False}

    class FakeClient:
        def connect(self):
            return True

        def get_accounts(self):
            return ["DU123456"]

        def disconnect(self):
            pass

    def fake_factory():
        called["value"] = True
        return FakeClient()

    monkeypatch.setattr(keepalive, "build_readonly_client", fake_factory)

    assert keepalive.check_api_readiness(timeout_seconds=0.1) is True
    assert called["value"] is True


def test_check_api_readiness_returns_false_when_api_check_times_out(capfd):
    class SlowClient:
        def connect(self):
            return True

        def get_accounts(self):
            time.sleep(0.2)
            return []

        def disconnect(self):
            pass

    result = keepalive.check_api_readiness(lambda: SlowClient(), timeout_seconds=0.05)

    assert result is False
    captured = capfd.readouterr()
    assert "API readiness check failed" in captured.out
    assert "timed out" in captured.out

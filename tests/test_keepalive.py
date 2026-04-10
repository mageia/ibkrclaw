import os
from importlib import util
from pathlib import Path


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
            pass

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

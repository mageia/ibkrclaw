from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _normalized_lines(relative_path: str) -> list[str]:
    path = REPO_ROOT / relative_path
    assert path.exists(), f"{relative_path} 不存在"
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        normalized = line.strip()
        if not normalized or normalized.startswith("#"):
            continue
        lines.append(normalized)
    return lines


def test_setup_script_removes_legacy_client_portal_stack():
    content = _read("scripts/setup.sh").lower()
    forbidden_tokens = [
        "clientportal",
        "ibeam",
        "chromedriver",
        "xvfb",
        "chromium-browser",
    ]

    for token in forbidden_tokens:
        assert token not in content, f"setup.sh 仍包含旧链路关键字: {token}"


def test_setup_script_contains_v2_runtime_defaults():
    content = _read("scripts/setup.sh")
    assert "ib_insync requests" in content
    assert "IB_HOST=127.0.0.1" in content


def test_skill_doc_targets_trading_script_and_keeps_runtime_env_keys():
    content = _read("SKILL.md")
    assert "/Users/" not in content
    assert "scripts/ibkr_trading.py" in content
    assert "scripts/ibkr_readonly.py" not in content
    assert "先确认再执行" in content
    for env_key in [
        "IB_HOST=127.0.0.1",
        "IB_PORT=4001",
        "IB_CLIENT_ID=1",
        "TG_BOT_TOKEN=",
        "TG_CHAT_ID=",
        "TG_NOTIFY_COOLDOWN=900",
    ]:
        assert env_key in content


def test_api_reference_is_explicitly_marked_deprecated_v1_client_portal():
    content = _read("references/api-endpoints.md")
    for marker in ["已弃用", "Deprecated", "v1", "Client Portal"]:
        assert marker in content, f"references/api-endpoints.md 缺少标记: {marker}"


def test_readme_switches_to_trading_entrypoint_and_confirmation_flow():
    content = _read("README.md")
    assert "clientportal/" not in content
    assert "ibkr_trading.py" in content
    assert "ibkr_readonly.py" not in content
    assert "keepalive.py" in content
    assert "先确认再执行" in content


def test_runtime_requirements_declare_runtime_and_dev_dependencies():
    runtime_lines = _normalized_lines("requirements.txt")
    assert sorted(runtime_lines) == ["ib_insync", "requests"]

    dev_lines = _normalized_lines("requirements-dev.txt")
    assert "-r requirements.txt" in dev_lines
    assert "pytest>=8,<9" in dev_lines
    assert sorted(dev_lines) == ["-r requirements.txt", "pytest>=8,<9"]


def test_ci_workflow_installs_requirements_and_runs_verification():
    workflow_lines = _normalized_lines(".github/workflows/python-tests.yml")
    workflow_text = "\n".join(workflow_lines)

    expected_fragments = [
        "actions/setup-python",
        "python-version:",
        "pip install -r requirements-dev.txt",
        "python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q",
        "python3 -m pytest tests/test_ibkr_trading.py -q",
        "python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py",
        "python3 -m py_compile scripts/ibkr_trading.py",
    ]

    for fragment in expected_fragments:
        assert fragment in workflow_text, f"{fragment} 未出现在 CI 工作流"

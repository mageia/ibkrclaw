from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


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


def test_skill_doc_uses_repo_relative_script_path():
    content = _read("SKILL.md")
    assert "/Users/" not in content
    assert "scripts/ibkr_readonly.py" in content


def test_api_reference_is_explicitly_marked_deprecated_v1_client_portal():
    content = _read("references/api-endpoints.md")
    for marker in ["已弃用", "Deprecated", "v1", "Client Portal"]:
        assert marker in content, f"references/api-endpoints.md 缺少标记: {marker}"


def test_readme_deployment_tree_removes_clientportal_and_keeps_v2_scripts():
    content = _read("README.md")
    tree_match = re.search(
        r"部署后在 `~/trading/` 目录下的文件：\s*```(?P<tree>.*?)```",
        content,
        re.DOTALL,
    )
    assert tree_match, "README.md 缺少部署目录代码块"

    tree = tree_match.group("tree")
    assert "clientportal/" not in tree
    assert "ibkr_readonly.py" in tree
    assert "keepalive.py" in tree

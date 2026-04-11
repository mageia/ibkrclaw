# IBKR Read-Only Phase 2 Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 IBKR 只读仓库补齐正式运行时依赖声明与 CI，让现有测试和 Phase 1 新增健康检查能力拥有可重复、自动化的工程保护。

**Architecture:** 保持现有脚本与测试结构不变，只补最小工程化骨架：`requirements.txt` 负责运行时依赖，`requirements-dev.txt` 通过包含 runtime 依赖扩展出开发测试依赖，GitHub Actions 只负责安装依赖、运行 `pytest` 和 `py_compile`。通过扩展 `tests/test_repo_alignment.py` 先锁定这些工程约束，再补齐配置文件，避免 CI 与依赖声明漂移。

**Tech Stack:** Python 3.9+, pytest, GitHub Actions, ib_insync, requests

---

## File Structure

- `requirements.txt`
  - 新增正式运行时依赖声明，只包含仓库运行所需最小集合
- `requirements-dev.txt`
  - 改为包含 `requirements.txt`，并追加测试依赖
- `.github/workflows/python-tests.yml`
  - 新增最小 CI 工作流，执行安装、测试和语法校验
- `tests/test_repo_alignment.py`
  - 扩展文本约束，锁定 requirements/CI 关键内容
- `tests/test_keepalive.py`
  - 本阶段原则上不再扩展业务行为测试，除非为工程化验证做极小契约修正

---

### Task 1: 用失败测试锁定依赖声明与 CI 约束

**Files:**
- Modify: `tests/test_repo_alignment.py`
- Test: `tests/test_repo_alignment.py`

- [ ] **Step 1: 先在 `tests/test_repo_alignment.py` 末尾追加依赖与 CI 红灯测试**

```python
def _normalized_lines(relative_path: str) -> list[str]:
    return [
        line.strip()
        for line in read(relative_path).splitlines()
        if line.strip()
    ]


def test_runtime_requirements_declare_runtime_and_dev_dependencies():
    runtime_lines = _normalized_lines("requirements.txt")
    dev_lines = _normalized_lines("requirements-dev.txt")

    assert runtime_lines == [
        "ib_insync",
        "requests",
    ]
    assert dev_lines == [
        "-r requirements.txt",
        "pytest>=8,<9",
    ]


def test_ci_workflow_installs_requirements_and_runs_verification():
    workflow = read(".github/workflows/python-tests.yml")

    assert "actions/setup-python" in workflow
    assert "python-version:" in workflow
    assert "pip install -r requirements-dev.txt" in workflow
    assert "python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q" in workflow
    assert "python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py" in workflow
```

- [ ] **Step 2: 运行红灯测试，确认当前仓库还不满足这些工程约束**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_repo_alignment.py::test_runtime_requirements_declare_runtime_and_dev_dependencies \
  tests/test_repo_alignment.py::test_ci_workflow_installs_requirements_and_runs_verification -q
```

Expected:

- FAIL
- 原因应包括：
  - `requirements.txt` 还不存在
  - `.github/workflows/python-tests.yml` 还不存在
  - `requirements-dev.txt` 仍未包含 `-r requirements.txt`

- [ ] **Step 3: 提交红灯测试**

```bash
git add tests/test_repo_alignment.py
git commit -m "test: lock engineering phase requirements"
```

---

### Task 2: 补齐运行时依赖声明

**Files:**
- Create: `requirements.txt`
- Modify: `requirements-dev.txt`
- Test: `tests/test_repo_alignment.py`

- [ ] **Step 1: 新建 `requirements.txt`**

创建 `requirements.txt`：

```text
ib_insync
requests
```

- [ ] **Step 2: 更新 `requirements-dev.txt` 让开发环境显式包含运行时依赖**

把 `requirements-dev.txt` 改成：

```text
-r requirements.txt
pytest>=8,<9
```

- [ ] **Step 3: 重新运行依赖约束测试，确认第一部分变绿**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_repo_alignment.py::test_runtime_requirements_declare_runtime_and_dev_dependencies -q
```

Expected:

- PASS（1 passed）

- [ ] **Step 4: 用新的依赖文件重新安装当前 worktree 的测试环境**

Run:

```bash
source .venv/bin/activate && python3 -m pip install -r requirements-dev.txt
```

Expected:

- 成功安装或确认 `ib_insync`、`requests`、`pytest`

- [ ] **Step 5: 提交依赖声明改动**

```bash
git add requirements.txt requirements-dev.txt
git commit -m "build: add runtime dependency declarations"
```

---

### Task 3: 用失败测试锁定最小 CI 工作流

**Files:**
- Modify: `tests/test_repo_alignment.py`
- Test: `tests/test_repo_alignment.py`

- [ ] **Step 1: 运行 CI 约束测试，确认现在依然是红灯**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_repo_alignment.py::test_ci_workflow_installs_requirements_and_runs_verification -q
```

Expected:

- FAIL
- 失败原因应明确是 `.github/workflows/python-tests.yml` 尚不存在或内容不匹配

- [ ] **Step 2: 提交这一步的红灯确认**

```bash
git add tests/test_repo_alignment.py
git commit --allow-empty -m "test: confirm ci workflow contract remains red"
```

---

### Task 4: 实现最小 GitHub Actions 工作流

**Files:**
- Create: `.github/workflows/python-tests.yml`
- Test: `tests/test_repo_alignment.py`

- [ ] **Step 1: 新建 `.github/workflows/python-tests.yml`**

创建文件：

```yaml
name: Python Tests

on:
  push:
    branches:
      - main
      - "feat/**"
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python3 -m pip install --upgrade pip
          python3 -m pip install -r requirements-dev.txt

      - name: Run unit tests
        run: |
          python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q

      - name: Run syntax checks
        run: |
          python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py
```

- [ ] **Step 2: 重新运行 CI 约束测试，确认变绿**

Run:

```bash
source .venv/bin/activate && python3 -m pytest \
  tests/test_repo_alignment.py::test_ci_workflow_installs_requirements_and_runs_verification -q
```

Expected:

- PASS（1 passed）

- [ ] **Step 3: 提交工作流**

```bash
git add .github/workflows/python-tests.yml
git commit -m "ci: add python verification workflow"
```

---

### Task 5: 阶段 2 最终验证

**Files:**
- Create: `requirements.txt`
- Modify: `requirements-dev.txt`
- Create: `.github/workflows/python-tests.yml`
- Modify: `tests/test_repo_alignment.py`

- [ ] **Step 1: 跑阶段 2 全量测试**

Run:

```bash
source .venv/bin/activate && python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q
```

Expected:

- PASS（至少 23 passed）

- [ ] **Step 2: 跑 Python 语法校验**

Run:

```bash
python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py
```

Expected:

- 无输出
- 退出码 0

- [ ] **Step 3: 手动检查工程化文件已到位**

Run:

```bash
printf '%s\n' '--- requirements.txt ---' && cat requirements.txt && \
printf '\n%s\n' '--- requirements-dev.txt ---' && cat requirements-dev.txt && \
printf '\n%s\n' '--- workflow ---' && sed -n '1,220p' .github/workflows/python-tests.yml
```

Expected:

- `requirements.txt` 仅包含 `ib_insync` 和 `requests`
- `requirements-dev.txt` 包含 `-r requirements.txt` 与 `pytest>=8,<9`
- workflow 中包含安装依赖、运行 pytest、运行 py_compile 的步骤

- [ ] **Step 4: 提交阶段收尾**

```bash
git add requirements.txt requirements-dev.txt .github/workflows/python-tests.yml tests/test_repo_alignment.py
git commit -m "feat: complete phase2 engineering hardening"
```

---

## Self-Review

### Spec Coverage

- 阶段 2 的 3 个目标都被覆盖：
  - `requirements.txt` 补齐 runtime 依赖
  - `requirements-dev.txt` 明确包含运行时依赖和 pytest
  - GitHub Actions workflow 提供自动化验证入口

### Placeholder Scan

- 未使用模糊语句或缺失实现说明
- 每个任务都包含明确文件内容、命令与期望结果

### Type Consistency

- 所有工程化断言统一使用：
  - `requirements.txt`
  - `requirements-dev.txt`
  - `.github/workflows/python-tests.yml`
- CI 工作流测试与 YAML 内容中的命令保持一致：
  - `python3 -m pytest tests/test_ibkr_readonly.py tests/test_keepalive.py tests/test_repo_alignment.py -q`
  - `python3 -m py_compile scripts/ibkr_readonly.py scripts/keepalive.py`

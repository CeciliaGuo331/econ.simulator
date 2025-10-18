"""Pytest configuration helpers for the econ simulator project.

Conventions and fixtures
- `client` : shared TestClient for synchronous web tests.
- `override_user` : helper to temporarily override the app's user dependency.
- `patch` : general-purpose alias for pytest's `monkeypatch` fixture. Prefer
    using `patch` in tests instead of naming the parameter `monkeypatch` so the
    intent is clearer and easier to refactor project-wide.
- `patch_orchestrator` : returns the `monkeypatch` object for tests that
    conceptually patch the orchestrator or orchestrator-related behavior.
- `patch_script_registry` : returns the `monkeypatch` object for tests that
    patch the script registry functions.
- `patch_views_orchestrator` : semantic wrapper for tests that patch objects
    attached to `econ_sim.web.views` (keeps tests explicit about their target).

When adding new tests, prefer one of the semantic fixtures (`patch_orchestrator`,
`patch_script_registry`, `patch_views_orchestrator`) when the test clearly targets
one of those subsystems; otherwise use `patch` as a generic replacement.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_project_root_on_path() -> None:
    project_root = Path(__file__).resolve().parents[1]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)


_ensure_project_root_on_path()

import asyncio
from typing import Callable, Dict

import pytest
from fastapi.testclient import TestClient
import os

from econ_sim.main import app
from econ_sim.web import views
from econ_sim.script_engine import sandbox
from econ_sim.script_engine import reset_script_registry


@pytest.fixture(scope="session", autouse=True)
def force_per_call_for_tests():
    """Force per-call subprocess execution during tests to ensure isolation."""
    # allow an override to test pool mode: set ECON_SIM_TEST_FORCE_POOL=1 to skip forcing per-call
    original = os.environ.get("ECON_SIM_FORCE_PER_CALL")
    if os.environ.get("ECON_SIM_TEST_FORCE_POOL") == "1":
        # skip forcing per-call to exercise pool mode
        yield
        return
    os.environ["ECON_SIM_FORCE_PER_CALL"] = "1"
    yield
    if original is None:
        os.environ.pop("ECON_SIM_FORCE_PER_CALL", None)
    else:
        os.environ["ECON_SIM_FORCE_PER_CALL"] = original


@pytest.fixture(scope="session")
def client():
    """提供共享的 TestClient 实例用于同步的 web 测试。

    示例：
        def test_homepage(client):
            resp = client.get("/")
            assert resp.status_code == 200
    说明：此 fixture 为 session 级别共享对象，避免在每个测试里重复创建 TestClient。
    """
    return TestClient(app)


@pytest.fixture
def override_user():
    """返回一个设置函数，用于在测试中临时替换请求用户的依赖注入。

    用法示例：
        def test_some_view(client, override_user):
            override_user({"email": "a@x.com", "user_type": "individual"})
            resp = client.get("/web/dashboard")
            assert resp.status_code == 200

    说明：
    - 会将视图依赖 `views._require_session_user`（及当 user_type 为 admin 时的
      `views._require_admin_user`）替换为返回给定用户的 lambda。测试结束时
      会自动恢复原有覆盖，避免污染其它测试。
    """

    original = dict(app.dependency_overrides)

    def _set(user: Dict[str, str]) -> None:
        app.dependency_overrides[views._require_session_user] = lambda: user
        if user.get("user_type") == "admin":
            app.dependency_overrides[views._require_admin_user] = lambda: user

    yield _set

    # teardown: 恢复原始覆盖
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


@pytest.fixture
def patch_orchestrator(monkeypatch):
    """辅助 fixture：用于替换或打桩与 orchestrator 相关的行为。

    用法示例：
        def test_logs(patch_orchestrator):
            patch_orchestrator.setattr(views, "_orchestrator", DummyOrchestrator())
            ...

    说明：这个 fixture 实际上返回 pytest 的 `monkeypatch` 对象，但在名字上
    更明确表示它通常用于修改 orchestrator 相关的全局对象，便于表达意图。
    """
    return monkeypatch


@pytest.fixture
def patch_script_registry(monkeypatch):
    """辅助 fixture：用于替换 `econ_sim.script_engine.script_registry` 中的函数或方法。

    用法示例：
        def test_list(patch_script_registry):
            patch_script_registry.setattr(script_registry, "list_scripts", fake_list)

    说明：当测试侧重于 script registry 的返回值或行为时使用，能让测试更语义化。
    """
    return monkeypatch


@pytest.fixture
def patch(monkeypatch):
    """通用的 `monkeypatch` 别名。

    用法示例：
        def test_something(patch):
            patch.setattr(target_module, "fn", fake_fn)

    说明：当测试没有明显属于某一语义组（如 orchestrator 或 script_registry）
    时，使用 `patch` 作为通用工具。采用别名有助于在将来统一管理替换行为。
    """
    return monkeypatch


@pytest.fixture
def patch_views_orchestrator(monkeypatch):
    """语义化 wrapper：用于显式指示将要 patch 的目标是 `econ_sim.web.views` 中的 orchestrator 或相关属性。

    用法示例：
        def test_view_behavior(patch_views_orchestrator):
            patch_views_orchestrator.setattr(views, "_orchestrator", DummyOrchestrator())

    说明：与 `patch_orchestrator` 类似，但名称更强调目标模块（views），便于阅读测试并快速知道替换的范围。
    """
    return monkeypatch


@pytest.fixture(scope="module", autouse=True)
def ensure_clean_pool_between_modules():
    """Module-scoped fixture that ensures the process pool is shutdown after
    each test module. This reduces cross-module interference from lingering
    worker processes created by the script sandbox.

    The fixture is best-effort: shutdown errors are ignored.
    """
    # no-op setup
    yield
    try:
        sandbox.shutdown_process_pool(wait=False)
    except Exception:
        # best-effort cleanup; swallow exceptions during teardown
        pass
    try:
        reset_script_registry()
    except Exception:
        pass

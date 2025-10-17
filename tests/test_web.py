from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, Optional
import urllib.parse

import pytest
import asyncio
import re

from fastapi.testclient import TestClient

from econ_sim.main import app
from econ_sim.web import views
from econ_sim.data_access.models import AgentKind
from econ_sim.script_engine import script_registry
from econ_sim.script_engine.registry import ScriptMetadata
from econ_sim.core.orchestrator import SimulationNotFoundError, SimulationStateError


SCRIPT_SOURCE = (
    "from econ_sim.script_engine.user_api import OverridesBuilder\n\n"
    "Context = dict[str, object]\n\n"
    "def generate_decisions(context: Context) -> dict[str, object]:\n"
    "    builder = OverridesBuilder()\n"
    "    return builder.build()\n"
)


def _build_world_state_dump(
    simulation_id: str = "sim-main",
    *,
    tick: int = 0,
    day: int = 0,
    households: Optional[Dict[int, Dict[str, object]]] = None,
) -> Dict[str, object]:
    household_payload = households or {
        1: {
            "id": 1,
            "balance_sheet": {
                "cash": 1200.0,
                "deposits": 600.0,
                "loans": 100.0,
                "inventory_goods": 0.0,
            },
            "skill": 1.1,
            "employment_status": "employed_firm",
            "labor_supply": 1.0,
            "wage_income": 450.0,
            "last_consumption": 320.0,
        }
    }

    return {
        "simulation_id": simulation_id,
        "tick": tick,
        "day": day,
        "households": household_payload,
        "firm": {
            "price": 10.0,
            "planned_production": 250.0,
            "wage_offer": 85.0,
            "employees": [1],
            "last_sales": 200.0,
            "balance_sheet": {
                "cash": 5000.0,
                "deposits": 2000.0,
                "loans": 1000.0,
                "inventory_goods": 80.0,
            },
        },
        "bank": {
            "deposit_rate": 0.01,
            "loan_rate": 0.05,
            "approved_loans": {"1": 500.0},
            "balance_sheet": {
                "cash": 20000.0,
                "deposits": 15000.0,
                "loans": 12000.0,
                "inventory_goods": 0.0,
            },
        },
        "government": {
            "tax_rate": 0.15,
            "unemployment_benefit": 60.0,
            "spending": 10000.0,
            "employees": [1, 2],
            "balance_sheet": {
                "cash": 8000.0,
                "deposits": 4000.0,
                "loans": 0.0,
            },
        },
        "central_bank": {
            "base_rate": 0.03,
            "reserve_ratio": 0.1,
            "inflation_target": 0.02,
            "unemployment_target": 0.05,
        },
        "macro": {
            "gdp": 123456.0,
            "inflation": 0.02,
            "unemployment_rate": 0.06,
            "price_index": 102.0,
            "wage_index": 101.0,
        },
        "features": {
            "household_shock_enabled": False,
            "household_shock_ability_std": 0.08,
            "household_shock_asset_std": 0.05,
            "household_shock_max_fraction": 0.4,
        },
        "household_shocks": {},
    }


@pytest.fixture
def client():
    return TestClient(app)


def _override_user(user):
    app.dependency_overrides[views._require_session_user] = lambda: user
    if user.get("user_type") == "admin":
        app.dependency_overrides[views._require_admin_user] = lambda: user


def _clear_override():
    app.dependency_overrides.pop(views._require_session_user, None)
    app.dependency_overrides.pop(views._require_admin_user, None)


def test_download_logs_success(monkeypatch, client):
    # 测试：当用户为 simulation 的参与者时，下载日志端点应返回 200 并包含日志内容与上下文。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    entries = [
        SimpleNamespace(tick=12, day=3, message="tick ok", context={"foo": "bar"}),
        SimpleNamespace(tick=13, day=3, message="tick warn", context=None),
    ]

    class DummyOrchestrator:
        async def list_participants(self, simulation_id):
            assert simulation_id == "sim-1"
            return ["player@example.com"]

        async def get_recent_logs(self, simulation_id, limit=None):
            assert simulation_id == "sim-1"
            assert limit == 500
            return entries

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    try:
        response = client.get("/web/logs/sim-1/download")
        assert response.status_code == 200
        assert "Day 3" in response.text
        assert 'context={"foo": "bar"}' in response.text
    finally:
        _clear_override()


def test_dashboard_displays_script_limit(client):
    # 测试：仪表盘页面应显示并反映指定 simulation 的脚本上限信息。
    async def _setup() -> None:
        await views._orchestrator.create_simulation("sim-limit")
        await script_registry.set_simulation_limit("sim-limit", 2)

    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(_setup())

    try:
        response = client.get("/web/dashboard?simulation_id=sim-limit")
        assert response.status_code == 200
        assert "当前脚本上限" in response.text
        assert re.search(r"脚本上限[\s\S]*2", response.text)
    finally:
        _clear_override()

        async def _cleanup() -> None:
            await script_registry.clear()
            try:
                await views._orchestrator.data_access.delete_simulation("sim-limit")
            except Exception:
                pass

        asyncio.run(_cleanup())


def test_upload_script_saved_to_library(client):
    # 测试：上传脚本到 /web/scripts 时，应把脚本保存到用户库（simulation_id 为空），并生成占位实体 ID。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())

    try:
        response = client.post(
            "/web/scripts",
            data={
                "current_simulation_id": "sim-upload",
                "description": "demo",
            },
            files={
                "script_file": ("demo.py", SCRIPT_SOURCE, "text/x-python"),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "simulation_id=sim-upload" in location

        scripts = asyncio.run(script_registry.list_user_scripts("player@example.com"))
        assert len(scripts) == 1
        assert scripts[0].simulation_id is None
        assert script_registry.is_placeholder_entity_id(scripts[0].entity_id)
        assert scripts[0].agent_kind is AgentKind.HOUSEHOLD
    finally:
        _clear_override()
        asyncio.run(script_registry.clear())


def test_detach_script_requires_tick_zero(monkeypatch, client):
    # 测试：在非 tick 0 时尝试从 simulation 分离脚本应被阻止并返回带 error 的重定向。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        )
    )
    asyncio.run(
        script_registry.attach_script(metadata.script_id, "sim-late", user["email"])
    )

    ticks = {"sim-late": 3}

    async def fake_get_state(simulation_id: str):
        if simulation_id not in ticks:
            raise SimulationNotFoundError()
        return SimpleNamespace(tick=ticks[simulation_id])

    monkeypatch.setattr(views._orchestrator, "get_state", fake_get_state)

    try:
        response = client.post(
            "/web/scripts/detach",
            data={
                "script_id": metadata.script_id,
                "simulation_id": "sim-late",
                "current_simulation_id": "sim-late",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "error=" in location

        meta_after = asyncio.run(
            script_registry.get_user_script(metadata.script_id, user["email"])
        )
        assert meta_after.simulation_id == "sim-late"
    finally:
        _clear_override()
        asyncio.run(script_registry.clear())


def test_detach_script_allows_tick_zero(monkeypatch, client):
    # 测试：在 tick 0 时允许从 simulation 分离脚本，分离后元数据中的 simulation_id 应为 None。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        )
    )
    asyncio.run(
        script_registry.attach_script(metadata.script_id, "sim-zero", user["email"])
    )

    ticks = {"sim-zero": 0}

    class DummyOrchestrator:
        async def get_state(self, simulation_id: str):
            if simulation_id not in ticks:
                raise SimulationNotFoundError()
            return SimpleNamespace(tick=ticks[simulation_id])

        async def detach_script_from_simulation(
            self, simulation_id: str, script_id: str, user_id: str
        ) -> None:
            assert simulation_id == "sim-zero"
            assert user_id == user["email"]
            await script_registry.detach_user_script(script_id, user_id)

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    try:
        response = client.post(
            "/web/scripts/detach",
            data={
                "script_id": metadata.script_id,
                "simulation_id": "sim-zero",
                "current_simulation_id": "sim-zero",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        meta_after = asyncio.run(
            script_registry.get_user_script(metadata.script_id, user["email"])
        )
        assert meta_after.simulation_id is None
    finally:
        _clear_override()
        asyncio.run(script_registry.clear())


def test_delete_script_unattached(client):
    # 测试：删除未绑定到 simulation 的用户脚本应成功并从用户库中移除。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        )
    )

    try:
        response = client.post(
            "/web/scripts/delete",
            data={
                "script_id": metadata.script_id,
                "current_simulation_id": "",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        scripts = asyncio.run(script_registry.list_user_scripts(user["email"]))
        assert not scripts
    finally:
        _clear_override()
        asyncio.run(script_registry.clear())


def test_delete_script_attached_requires_tick_zero(monkeypatch, client):
    # 测试：当脚本已绑定且 simulation 非 tick 0 时，删除操作应被阻止并返回错误重定向。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        )
    )
    asyncio.run(
        script_registry.attach_script(metadata.script_id, "sim-run", user["email"])
    )

    ticks = {"sim-run": 4}

    async def fake_get_state(simulation_id: str):
        if simulation_id not in ticks:
            raise SimulationNotFoundError()
        return SimpleNamespace(tick=ticks[simulation_id])

    monkeypatch.setattr(views._orchestrator, "get_state", fake_get_state)

    try:
        response = client.post(
            "/web/scripts/delete",
            data={
                "script_id": metadata.script_id,
                "current_simulation_id": "sim-run",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "error=" in location
        meta_after = asyncio.run(
            script_registry.get_user_script(metadata.script_id, user["email"])
        )
        assert meta_after.simulation_id == "sim-run"
    finally:
        _clear_override()
        asyncio.run(script_registry.clear())


def test_delete_script_attached_tick_zero(monkeypatch, client):
    # 测试：在 tick 0 时删除已绑定脚本应成功并从用户库中删除该脚本。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        )
    )
    asyncio.run(
        script_registry.attach_script(metadata.script_id, "sim-reset", user["email"])
    )

    ticks = {"sim-reset": 0}

    class DummyOrchestrator:
        async def get_state(self, simulation_id: str):
            if simulation_id not in ticks:
                raise SimulationNotFoundError()
            return SimpleNamespace(tick=ticks[simulation_id])

        async def remove_script_from_simulation(
            self, simulation_id: str, script_id: str
        ) -> None:
            assert simulation_id == "sim-reset"
            await script_registry.delete_user_script(script_id, user["email"])

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    try:
        response = client.post(
            "/web/scripts/delete",
            data={
                "script_id": metadata.script_id,
                "current_simulation_id": "sim-reset",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        scripts = asyncio.run(script_registry.list_user_scripts(user["email"]))
        assert not scripts
    finally:
        _clear_override()
        asyncio.run(script_registry.clear())


def test_admin_delete_script_blocked_when_simulation_running(monkeypatch, client):
    # 测试：管理员尝试删除 simulation 中的脚本但 simulation 正在运行（非 tick 0）时应收到合适的错误提示。
    admin_user = {"email": "admin@example.com", "user_type": "admin"}
    _override_user(admin_user)

    class DummyOrchestrator:
        async def remove_script_from_simulation(self, simulation_id, script_id):
            raise SimulationStateError(simulation_id, 5)

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    try:
        response = client.post(
            "/web/admin/scripts/delete",
            data={
                "simulation_id": "sim-live",
                "script_id": "script-123",
                "current_simulation_id": "sim-live",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert location
        parsed = urllib.parse.urlparse(location)
        assert parsed.path == "/web/dashboard"
        params = urllib.parse.parse_qs(parsed.query)
        assert params.get("simulation_id") == ["sim-live"]
        assert params.get("error") == [
            "仿真实例 sim-live 已运行到 tick 5，仅在 tick 0 时允许删除挂载的脚本。"
        ]
    finally:
        _clear_override()


def test_user_dashboard_displays_role_tables(monkeypatch, client):
    # 测试：用户仪表盘应展示角色相关表格以及关键指标（家庭、市场等）。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    households = {
        1: {
            "id": 1,
            "balance_sheet": {
                "cash": 1500.0,
                "deposits": 500.0,
                "loans": 120.0,
                "inventory_goods": 0.0,
            },
            "skill": 1.2,
            "employment_status": "employed_firm",
            "labor_supply": 1.0,
            "wage_income": 480.0,
            "last_consumption": 350.0,
        },
        2: {
            "id": 2,
            "balance_sheet": {
                "cash": 900.0,
                "deposits": 200.0,
                "loans": 60.0,
                "inventory_goods": 0.0,
            },
            "skill": 0.95,
            "employment_status": "unemployed",
            "labor_supply": 1.0,
            "wage_income": 150.0,
            "last_consumption": 260.0,
        },
    }

    class DummyWorldState:
        def model_dump(self, mode: str = "json"):
            return _build_world_state_dump(
                "sim-main", tick=2, day=1, households=households
            )

    class DummyOrchestrator:
        async def list_simulations(self):
            return ["sim-main"]

        async def get_state(self, simulation_id: str):
            assert simulation_id == "sim-main"
            return DummyWorldState()

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    user_scripts = [
        ScriptMetadata(
            script_id="script-001",
            simulation_id="sim-main",
            user_id=user["email"],
            description="主要策略",
            created_at=now,
            code_version="1.0",
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        ),
        ScriptMetadata(
            script_id="script-002",
            simulation_id=None,
            user_id=user["email"],
            description="备用策略",
            created_at=now,
            code_version="1.1",
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="2",
        ),
    ]

    async def fake_list_user_scripts(email: str):
        assert email == user["email"]
        return user_scripts

    async def fake_list_scripts(simulation_id: str):
        assert simulation_id == "sim-main"
        return [user_scripts[0]]

    async def fake_get_simulation_limit(simulation_id: str):
        assert simulation_id == "sim-main"
        return 3

    monkeypatch.setattr(script_registry, "list_user_scripts", fake_list_user_scripts)
    monkeypatch.setattr(script_registry, "list_scripts", fake_list_scripts)
    monkeypatch.setattr(
        script_registry, "get_simulation_limit", fake_get_simulation_limit
    )

    try:
        response = client.get("/web/dashboard?simulation_id=sim-main")
        assert response.status_code == 200
        body = response.text
        assert "角色视角数据" in body
        assert "市场价格与利率" in body
        assert "家户平均指标" in body
        assert "平均现金" in body
        assert "1,200.00" in body
        assert "就业率" in body
    finally:
        _clear_override()


def test_admin_dashboard_displays_snapshot_tables(monkeypatch, client):
    # 测试：管理员仪表盘应列出各 simulation 的快照信息、用户与脚本统计等。
    admin_user = {"email": "admin@example.com", "user_type": "admin"}
    _override_user(admin_user)

    households = {
        1: {
            "id": 1,
            "balance_sheet": {
                "cash": 1800.0,
                "deposits": 700.0,
                "loans": 150.0,
                "inventory_goods": 10.0,
            },
            "skill": 1.0,
            "employment_status": "employed_firm",
            "labor_supply": 1.0,
            "wage_income": 500.0,
            "last_consumption": 330.0,
        }
    }

    class DummyFeatures:
        def __init__(self, enabled: bool) -> None:
            self.household_shock_enabled = enabled

        def model_dump(self, mode: str = "json"):
            return {
                "household_shock_enabled": self.household_shock_enabled,
                "household_shock_ability_std": 0.08,
                "household_shock_asset_std": 0.05,
                "household_shock_max_fraction": 0.4,
            }

    class DummyWorldState:
        def __init__(self, simulation_id: str) -> None:
            self.simulation_id = simulation_id

        def model_dump(self, mode: str = "json"):
            return _build_world_state_dump(
                self.simulation_id, tick=4, day=2, households=households
            )

    class DummyOrchestrator:
        async def list_simulations(self):
            return ["sim-alpha", "sim-beta"]

        async def get_simulation_features(self, simulation_id: str):
            return DummyFeatures(enabled=simulation_id == "sim-beta")

        async def get_state(self, simulation_id: str):
            return DummyWorldState(simulation_id)

        async def list_recent_script_failures(self, simulation_id, limit=None):
            return []

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    scripts = [
        ScriptMetadata(
            script_id="household-script",
            simulation_id="sim-alpha",
            user_id="indy@example.com",
            description="居民策略",
            created_at=now,
            code_version="1.0",
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        ),
        ScriptMetadata(
            script_id="firm-script",
            simulation_id="sim-beta",
            user_id="firm@example.com",
            description="企业策略",
            created_at=now,
            code_version="2.0",
            agent_kind=AgentKind.FIRM,
            entity_id="firm_1",
        ),
    ]

    async def fake_list_all_scripts():
        return scripts

    async def fake_get_simulation_limit(simulation_id: str):
        return {"sim-alpha": 2}.get(simulation_id)

    async def fake_list_users():
        base_time = datetime(2024, 5, 1, tzinfo=timezone.utc)
        return [
            SimpleNamespace(
                email="admin@example.com",
                created_at=base_time,
                user_type="admin",
            ),
            SimpleNamespace(
                email="indy@example.com",
                created_at=base_time,
                user_type="individual",
            ),
            SimpleNamespace(
                email="firm@example.com",
                created_at=base_time,
                user_type="firm",
            ),
        ]

    async def fake_list_scripts(simulation_id: str):
        return [s for s in scripts if s.simulation_id == simulation_id]

    monkeypatch.setattr(script_registry, "list_all_scripts", fake_list_all_scripts)
    monkeypatch.setattr(
        script_registry, "get_simulation_limit", fake_get_simulation_limit
    )
    monkeypatch.setattr(script_registry, "list_scripts", fake_list_scripts)
    monkeypatch.setattr(views.user_manager, "list_users", fake_list_users)

    try:
        response = client.get("/web/dashboard")
        assert response.status_code == 200
        body = response.text
        assert "世界状态快照" in body
        assert "仿真进度" in body
        assert "宏观指标" in body
        assert "家户样本（前 8 户）" in body
        assert "脚本功能开关" in body
    finally:
        _clear_override()


def test_admin_can_update_script_limit(monkeypatch, client):
    # 测试：管理员可以通过表单更新指定 simulation 的脚本上限，并能收到确认重定向。
    admin_user = {"email": "admin@example.com", "user_type": "admin"}
    _override_user(admin_user)

    calls = {}

    class DummyOrchestrator:
        async def set_script_limit(self, simulation_id, limit):
            calls["called"] = (simulation_id, limit)
            return limit

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    try:
        response = client.post(
            "/web/admin/simulations/script_limit",
            data={
                "simulation_id": "sim-42",
                "max_scripts_per_user": "5",
                "submit_action": "apply",
                "current_simulation_id": "sim-42",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert calls.get("called") == ("sim-42", 5)
        location = response.headers.get("location", "")
        assert location.startswith("/web/dashboard")
        assert "simulation_id=sim-42" in location
    finally:
        _clear_override()


def test_admin_dashboard_displays_household_counts(monkeypatch, client):
    # 测试：管理员仪表盘应显示按家庭计数的挂载脚本数和相关 DOM 元素。
    admin_user = {"email": "admin@example.com", "user_type": "admin"}
    _override_user(admin_user)

    class DummyFeatures:
        def model_dump(self, mode: str = "json"):
            return {"household_shock_enabled": False}

    class DummyWorldState:
        def __init__(self) -> None:
            self.tick = 0
            self.day = 0

        def model_dump(self, mode: str = "json"):
            return _build_world_state_dump("sim-main", tick=self.tick, day=self.day)

    class DummyOrchestrator:
        async def list_simulations(self):
            return ["sim-main"]

        async def get_simulation_features(self, simulation_id):
            assert simulation_id == "sim-main"
            return DummyFeatures()

        async def list_participants(self, simulation_id):
            assert simulation_id == "sim-main"
            return ["household@example.com", "firm@example.com"]

        async def get_state(self, simulation_id):
            assert simulation_id == "sim-main"
            return DummyWorldState()

        async def list_recent_script_failures(self, simulation_id, limit=None):
            return []

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    async def fake_list_users():
        base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return [
            SimpleNamespace(
                email="household@example.com",
                created_at=base_time,
                user_type="individual",
            ),
            SimpleNamespace(
                email="firm@example.com",
                created_at=base_time,
                user_type="firm",
            ),
            SimpleNamespace(
                email="admin@example.com",
                created_at=base_time,
                user_type="admin",
            ),
        ]

    async def fake_list_all_scripts():
        return []

    async def fake_list_scripts(simulation_id):
        assert simulation_id == "sim-main"
        return []

    monkeypatch.setattr(views.user_manager, "list_users", fake_list_users)
    monkeypatch.setattr(script_registry, "list_all_scripts", fake_list_all_scripts)
    monkeypatch.setattr(script_registry, "list_scripts", fake_list_scripts)

    try:
        response = client.get("/web/dashboard?simulation_id=sim-main")
        assert response.status_code == 200
        assert "挂载家户脚本数" in response.text
        assert 'class="household-count"' in response.text
    finally:
        _clear_override()


def test_admin_dashboard_household_counts_include_scripts(monkeypatch, client):
    # 测试：家庭计数统计中应包含每个家庭挂载的脚本数量，页面应反映出脚本计数。
    admin_user = {"email": "admin@example.com", "user_type": "admin"}
    _override_user(admin_user)

    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    script_meta_one = ScriptMetadata(
        script_id="script-1",
        simulation_id="sim-main",
        user_id="household@example.com",
        description=None,
        created_at=base_time,
        code_version="v1",
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="1",
    )
    script_meta_two = ScriptMetadata(
        script_id="script-2",
        simulation_id="sim-main",
        user_id="household@example.com",
        description=None,
        created_at=base_time,
        code_version="v2",
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="2",
    )

    class DummyFeatures:
        def model_dump(self, mode: str = "json"):
            return {"household_shock_enabled": False}

    class DummyWorldState:
        def __init__(self, simulation_id: str) -> None:
            self.simulation_id = simulation_id

        def model_dump(self, mode: str = "json"):
            return _build_world_state_dump(self.simulation_id)

    class DummyOrchestrator:
        async def list_simulations(self):
            return ["sim-main"]

        async def get_simulation_features(self, simulation_id):
            assert simulation_id == "sim-main"
            return DummyFeatures()

        async def list_participants(self, simulation_id):
            assert simulation_id == "sim-main"
            return []

        async def get_state(self, simulation_id):
            assert simulation_id == "sim-main"
            return DummyWorldState(simulation_id)

        async def list_recent_script_failures(
            self, simulation_id: str, limit: int = 50
        ):
            assert simulation_id == "sim-main"
            return []

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    async def fake_list_users():
        return [
            SimpleNamespace(
                email="household@example.com",
                created_at=base_time,
                user_type="individual",
            )
        ]

    async def fake_list_all_scripts():
        return [script_meta_one, script_meta_two]

    async def fake_list_scripts(simulation_id):
        assert simulation_id == "sim-main"
        return [script_meta_one, script_meta_two]

    monkeypatch.setattr(views.user_manager, "list_users", fake_list_users)
    monkeypatch.setattr(script_registry, "list_all_scripts", fake_list_all_scripts)
    monkeypatch.setattr(script_registry, "list_scripts", fake_list_scripts)

    try:
        response = client.get("/web/dashboard?simulation_id=sim-main")
        assert response.status_code == 200
        assert re.search(r'class="household-count"[^>]*>\s*1\s*户', response.text)
    finally:
        _clear_override()


def test_attach_script_registers_participant(client):
    # 测试：将已上传脚本挂载到 simulation 时，应把脚本的 simulation_id 更新，并将用户注册为参与者。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())

    async def _setup() -> ScriptMetadata:
        await views._orchestrator.create_simulation("sim-attach")
        return await script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        )

    metadata = asyncio.run(_setup())

    try:
        response = client.post(
            "/web/scripts/attach",
            data={
                "simulation_id": "sim-attach",
                "script_id": metadata.script_id,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        meta_after = asyncio.run(
            script_registry.get_user_script(metadata.script_id, user["email"])
        )
        assert meta_after.simulation_id == "sim-attach"
        participants = asyncio.run(views._orchestrator.list_participants("sim-attach"))
        assert user["email"] in participants
    finally:
        _clear_override()

        async def _cleanup() -> None:
            await script_registry.clear()
            try:
                await views._orchestrator.data_access.delete_simulation("sim-attach")
            except Exception:
                pass

        asyncio.run(_cleanup())


def test_admin_dashboard_lists_all_scripts(monkeypatch, client):
    # 测试：管理员视图应列出所有脚本并能显示脚本标识，且不会显示“无脚本”占位文本。
    admin_user = {"email": "admin@example.com", "user_type": "admin"}
    _override_user(admin_user)

    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    script_meta = ScriptMetadata(
        script_id="script-visible",
        simulation_id="sim-main",
        user_id="household@example.com",
        description="demo script",
        created_at=base_time,
        code_version="v1",
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="1",
    )

    class DummyFeatures:
        def model_dump(self, mode: str = "json"):
            return {"household_shock_enabled": False}

    class DummyWorldState:
        def __init__(self, simulation_id: str) -> None:
            self.simulation_id = simulation_id

        def model_dump(self, mode: str = "json"):
            return _build_world_state_dump(self.simulation_id)

    class DummyOrchestrator:
        async def list_simulations(self):
            return ["sim-main"]

        async def get_simulation_features(self, simulation_id):
            assert simulation_id == "sim-main"
            return DummyFeatures()

        async def list_participants(self, simulation_id):
            assert simulation_id == "sim-main"
            return []

        async def get_state(self, simulation_id):
            assert simulation_id == "sim-main"
            return DummyWorldState(simulation_id)

        async def list_recent_script_failures(
            self, simulation_id: str, limit: int = 50
        ):
            assert simulation_id == "sim-main"
            return []

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    async def fake_list_users():
        return [
            SimpleNamespace(
                email="household@example.com",
                created_at=base_time,
                user_type="individual",
            )
        ]

    async def fake_list_all_scripts():
        return [script_meta]

    async def fake_list_scripts(simulation_id):
        assert simulation_id == "sim-main"
        return [script_meta]

    monkeypatch.setattr(views.user_manager, "list_users", fake_list_users)
    monkeypatch.setattr(script_registry, "list_all_scripts", fake_list_all_scripts)
    monkeypatch.setattr(script_registry, "list_scripts", fake_list_scripts)

    try:
        response = client.get("/web/dashboard?simulation_id=sim-main")
        assert response.status_code == 200
        assert "script-visible" in response.text
        assert "暂时没有上传脚本" not in response.text
    finally:
        _clear_override()


def test_attach_script_respects_limit(client):
    # 测试：当 simulation 对单用户脚本数有限制时，超出限制的挂载请求应被拒绝并保留脚本为未绑定。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())

    async def _setup() -> tuple[ScriptMetadata, ScriptMetadata]:
        await views._orchestrator.create_simulation("sim-limit-attach")
        await script_registry.set_simulation_limit("sim-limit-attach", 1)
        first = await script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        )
        second = await script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="2",
        )
        return first, second

    script_one, script_two = asyncio.run(_setup())

    try:
        # First attach should succeed
        response_ok = client.post(
            "/web/scripts/attach",
            data={
                "simulation_id": "sim-limit-attach",
                "script_id": script_one.script_id,
            },
            follow_redirects=False,
        )
        assert response_ok.status_code == 303

        # Second attach should hit limit
        response_fail = client.post(
            "/web/scripts/attach",
            data={
                "simulation_id": "sim-limit-attach",
                "script_id": script_two.script_id,
            },
            follow_redirects=False,
        )
        assert response_fail.status_code == 303
        location = response_fail.headers.get("location", "")
        assert "error=" in location

        meta_after_second = asyncio.run(
            script_registry.get_user_script(script_two.script_id, user["email"])
        )
        assert meta_after_second.simulation_id is None
    finally:
        _clear_override()

        async def _cleanup() -> None:
            await script_registry.clear()
            try:
                await views._orchestrator.data_access.delete_simulation(
                    "sim-limit-attach"
                )
            except Exception:
                pass

        asyncio.run(_cleanup())


def test_download_logs_forbidden(monkeypatch, client):
    # 测试：非参与者访问下载日志接口应返回 403 并且不触发日志检索调用。
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    class DummyOrchestrator:
        async def list_participants(self, simulation_id):
            return ["other@example.com"]

        async def get_recent_logs(self, simulation_id, limit=None):
            pytest.fail("should not fetch logs when user is not a participant")

    monkeypatch.setattr(views, "_orchestrator", DummyOrchestrator())

    try:
        response = client.get("/web/logs/sim-1/download")
        assert response.status_code == 403
    finally:
        _clear_override()

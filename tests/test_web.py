from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import asyncio
import re

from fastapi.testclient import TestClient

from econ_sim.main import app
from econ_sim.web import views
from econ_sim.script_engine import script_registry
from econ_sim.script_engine.registry import ScriptMetadata
from econ_sim.core.orchestrator import SimulationNotFoundError


SCRIPT_SOURCE = (
    "from econ_sim.script_engine.user_api import OverridesBuilder\n\n"
    "Context = dict[str, object]\n\n"
    "def generate_decisions(context: Context) -> dict[str, object]:\n"
    "    builder = OverridesBuilder()\n"
    "    return builder.build()\n"
)


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
    finally:
        _clear_override()
        asyncio.run(script_registry.clear())


def test_detach_script_requires_tick_zero(monkeypatch, client):
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
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
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
        )
    )
    asyncio.run(
        script_registry.attach_script(metadata.script_id, "sim-zero", user["email"])
    )

    ticks = {"sim-zero": 0}

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
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
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
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
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
    user = {"email": "player@example.com", "user_type": "individual"}
    _override_user(user)

    asyncio.run(script_registry.clear())
    metadata = asyncio.run(
        script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
        )
    )
    asyncio.run(
        script_registry.attach_script(metadata.script_id, "sim-reset", user["email"])
    )

    ticks = {"sim-reset": 0}

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


def test_admin_can_update_script_limit(monkeypatch, client):
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
    admin_user = {"email": "admin@example.com", "user_type": "admin"}
    _override_user(admin_user)

    class DummyFeatures:
        def model_dump(self, mode: str = "json"):
            return {"household_shock_enabled": False}

    class DummyWorldState:
        def model_dump(self, mode: str = "json"):
            return {
                "tick": 0,
                "day": 0,
                "world": {},
                "features": {"household_shock_enabled": False},
            }

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
        assert "当前家户个数" in response.text
        assert 'class="household-count"' in response.text
        assert re.search(r'class="household-count"[^>]*>\s*1\s*户', response.text)
    finally:
        _clear_override()


def test_admin_dashboard_household_counts_include_scripts(monkeypatch, client):
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
    )
    script_meta_two = ScriptMetadata(
        script_id="script-2",
        simulation_id="sim-main",
        user_id="household@example.com",
        description=None,
        created_at=base_time,
        code_version="v2",
    )

    class DummyFeatures:
        def model_dump(self, mode: str = "json"):
            return {"household_shock_enabled": False}

    class DummyWorldState:
        def model_dump(self, mode: str = "json"):
            return {
                "tick": 0,
                "day": 0,
                "world": {},
                "features": {"household_shock_enabled": False},
            }

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
            return DummyWorldState()

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
        assert re.search(r'class="household-count"[^>]*>\s*2\s*户', response.text)
    finally:
        _clear_override()


def test_attach_script_registers_participant(client):
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
    )

    class DummyFeatures:
        def model_dump(self, mode: str = "json"):
            return {"household_shock_enabled": False}

    class DummyWorldState:
        def model_dump(self, mode: str = "json"):
            return {
                "tick": 0,
                "day": 0,
                "world": {},
                "features": {"household_shock_enabled": False},
            }

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
            return DummyWorldState()

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
        )
        second = await script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=SCRIPT_SOURCE,
            description=None,
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

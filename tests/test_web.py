from types import SimpleNamespace

import pytest
import asyncio
import re

from fastapi.testclient import TestClient

from econ_sim.main import app
from econ_sim.web import views
from econ_sim.script_engine import script_registry


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

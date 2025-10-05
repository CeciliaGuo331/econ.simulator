import pytest
from httpx import ASGITransport, AsyncClient

from econ_sim.auth import user_manager
from econ_sim.auth.user_manager import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD
from econ_sim.core.orchestrator import SimulationNotFoundError, SimulationOrchestrator
from econ_sim.data_access.models import (
    FirmDecisionOverride,
    HouseholdDecisionOverride,
    TickDecisionOverrides,
)
from econ_sim.main import app
from econ_sim.script_engine import script_registry


@pytest.mark.asyncio
async def test_tick_progression_increments() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "test_sim"
    state = await orchestrator.create_simulation(simulation_id)
    result = await orchestrator.run_tick(simulation_id)

    assert result.world_state.tick == state.tick + 1
    assert result.world_state.day >= state.day
    assert result.world_state.macro.gdp >= 0.0


@pytest.mark.asyncio
async def test_overrides_affect_decisions() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "override_sim"
    await orchestrator.create_simulation(simulation_id)

    overrides = TickDecisionOverrides(
        households={
            0: HouseholdDecisionOverride(consumption_budget=0.0, savings_rate=0.0)
        },
        firm=FirmDecisionOverride(price=25.0),
    )

    result = await orchestrator.run_tick(simulation_id, overrides=overrides)

    assert result.world_state.households[0].last_consumption <= 0.5
    assert abs(result.world_state.firm.price - 25.0) < 1e-6


@pytest.mark.asyncio
async def test_reset_simulation_restores_initial_state() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "reset-sim"

    await orchestrator.create_simulation(simulation_id)
    await orchestrator.register_participant(simulation_id, "player@example.com")

    script_registry.clear()
    try:
        script_registry.register_script(
            simulation_id=simulation_id,
            user_id="player@example.com",
            script_code="""
def generate_decisions(context):
    return {}
""",
            description="noop",
        )

        await orchestrator.run_tick(simulation_id)

        reset_state = await orchestrator.reset_simulation(simulation_id)

        config = orchestrator.config.simulation
        assert reset_state.tick == config.initial_tick
        assert reset_state.day == config.initial_day

        participants = await orchestrator.list_participants(simulation_id)
        assert participants == ["player@example.com"]

        scripts = script_registry.list_scripts(simulation_id)
        assert len(scripts) == 1
        assert scripts[0].description == "noop"
    finally:
        script_registry.clear()


@pytest.mark.asyncio
async def test_delete_simulation_detaches_associations() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "delete-sim"

    await orchestrator.create_simulation(simulation_id)
    await orchestrator.register_participant(simulation_id, "player@example.com")

    script_registry.clear()
    try:
        metadata = script_registry.register_script(
            simulation_id=simulation_id,
            user_id="player@example.com",
            script_code="""
def generate_decisions(context):
    return {}
""",
        )

        result = await orchestrator.delete_simulation(simulation_id)

        assert result["participants_removed"] == 1
        assert result["scripts_detached"] == 1

        with pytest.raises(SimulationNotFoundError):
            await orchestrator.get_state(simulation_id)

        assert script_registry.list_scripts(simulation_id) == []

        # ensure metadata reference is no longer tracked
        assert metadata.script_id not in {
            m.script_id for m in script_registry.list_all_scripts()
        }
    finally:
        script_registry.clear()


@pytest.mark.asyncio
async def test_admin_restrictions_for_simulation_control() -> None:
    await user_manager.reset()
    script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 普通用户注册并登录
        await client.post(
            "/auth/register",
            json={
                "email": "player@test.com",
                "password": "StrongPass123",
                "user_type": "individual",
            },
        )
        user_login = await client.post(
            "/auth/login",
            json={"email": "player@test.com", "password": "StrongPass123"},
        )
        user_token = user_login.json()["access_token"]

        admin_login = await client.post(
            "/auth/login",
            json={
                "email": DEFAULT_ADMIN_EMAIL,
                "password": DEFAULT_ADMIN_PASSWORD,
            },
        )
        admin_token = admin_login.json()["access_token"]

        headers_user = {"Authorization": f"Bearer {user_token}"}
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        simulation_id = "admin-permission-sim"

        denied = await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_user,
        )
        assert denied.status_code == 403

        created = await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )
        assert created.status_code == 200

        forbidden_run = await client.post(
            f"/simulations/{simulation_id}/run_tick",
            json={},
            headers=headers_user,
        )
        assert forbidden_run.status_code == 403

        allowed_run = await client.post(
            f"/simulations/{simulation_id}/run_tick",
            json={},
            headers=headers_admin,
        )
        assert allowed_run.status_code == 200


@pytest.mark.asyncio
async def test_script_upload_requires_existing_simulation() -> None:
    await user_manager.reset()
    script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={
                "email": "scripter@test.com",
                "password": "StrongPass123",
                "user_type": "firm",
            },
        )
        user_login = await client.post(
            "/auth/login",
            json={"email": "scripter@test.com", "password": "StrongPass123"},
        )
        user_token = user_login.json()["access_token"]
        admin_login = await client.post(
            "/auth/login",
            json={
                "email": DEFAULT_ADMIN_EMAIL,
                "password": DEFAULT_ADMIN_PASSWORD,
            },
        )
        admin_token = admin_login.json()["access_token"]

        headers_user = {"Authorization": f"Bearer {user_token}"}
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        script_code = """
def generate_decisions(context):
    return {}
"""

        missing = await client.post(
            "/simulations/missing-sim/scripts",
            json={"code": script_code},
            headers=headers_user,
        )
        assert missing.status_code == 404

        simulation_id = "script-upload-sim"
        await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )

        upload = await client.post(
            f"/simulations/{simulation_id}/scripts",
            json={"code": script_code, "description": "test"},
            headers=headers_user,
        )
        assert upload.status_code == 200
        payload = upload.json()
        assert payload["message"] == "Script registered successfully."


@pytest.mark.asyncio
async def test_day_rollover_starting_from_tick_one() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "day-rollover"
    initial = await orchestrator.create_simulation(simulation_id)

    first_tick = await orchestrator.run_tick(simulation_id)
    assert first_tick.world_state.day == initial.day + 1

    second_tick = await orchestrator.run_tick(simulation_id)
    assert second_tick.world_state.day == first_tick.world_state.day

    third_tick = await orchestrator.run_tick(simulation_id)
    assert third_tick.world_state.day == first_tick.world_state.day

    fourth_tick = await orchestrator.run_tick(simulation_id)
    assert fourth_tick.world_state.day == first_tick.world_state.day + 1

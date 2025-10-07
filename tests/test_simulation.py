from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from econ_sim.auth import user_manager
from econ_sim.auth.user_manager import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD
from econ_sim.core.orchestrator import (
    SimulationNotFoundError,
    SimulationOrchestrator,
    SimulationStateError,
)
from econ_sim.data_access.models import (
    AgentKind,
    FirmDecisionOverride,
    HouseholdDecisionOverride,
    TickDecisionOverrides,
)
from econ_sim.main import app
from econ_sim.utils.settings import get_world_config
from econ_sim.script_engine import script_registry
from econ_sim.script_engine.registry import ScriptFailureEvent
from tests.utils import seed_required_scripts


class StubFailureNotifier:
    def __init__(self) -> None:
        self.events: list[ScriptFailureEvent] = []

    def notify(self, event: ScriptFailureEvent) -> None:
        self.events.append(event)


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
async def test_household_shock_toggle_updates_state() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "shock-toggle"

    await orchestrator.create_simulation(simulation_id)
    updated = await orchestrator.update_simulation_features(
        simulation_id,
        household_shock_enabled=True,
        household_shock_ability_std=0.1,
        household_shock_asset_std=0.05,
        household_shock_max_fraction=0.3,
    )

    assert updated.features.household_shock_enabled is True
    assert updated.features.household_shock_ability_std == pytest.approx(0.1)

    tick_result = await orchestrator.run_tick(simulation_id)
    assert tick_result.world_state.features.household_shock_enabled is True
    assert (
        len(tick_result.world_state.household_shocks)
        == orchestrator.config.simulation.num_households
    )

    # 重置仿真实例以回到 tick 0，再关闭外生冲击
    await orchestrator.reset_simulation(simulation_id)

    disabled = await orchestrator.update_simulation_features(
        simulation_id,
        household_shock_enabled=False,
    )
    assert disabled.features.household_shock_enabled is False

    result_without_shock = await orchestrator.run_tick(simulation_id)
    assert not result_without_shock.world_state.household_shocks


@pytest.mark.asyncio
async def test_remove_script_from_simulation_requires_tick_zero() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "tick-guard-remove"

    await orchestrator.create_simulation(simulation_id)

    await script_registry.clear()
    try:
        metadata = await orchestrator.register_script_for_simulation(
            simulation_id=simulation_id,
            user_id="player@example.com",
            script_code="""
def generate_decisions(context):
    return {}
""",
            description="delete-test",
        )

        await orchestrator.run_tick(simulation_id)

        with pytest.raises(SimulationStateError):
            await orchestrator.remove_script_from_simulation(
                simulation_id=simulation_id,
                script_id=metadata.script_id,
            )

        scripts = await script_registry.list_scripts(simulation_id)
        assert any(script.script_id == metadata.script_id for script in scripts)
    finally:
        await script_registry.clear()


@pytest.mark.asyncio
async def test_run_until_day_executes_required_ticks() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "run-days"

    initial_state = await orchestrator.create_simulation(simulation_id)
    batch = await orchestrator.run_until_day(simulation_id, 2)

    ticks_per_day = orchestrator.config.simulation.ticks_per_day
    assert batch.world_state.day >= initial_state.day + 2
    assert batch.ticks_executed == 2 * ticks_per_day
    assert batch.world_state.tick == initial_state.tick + batch.ticks_executed


@pytest.mark.asyncio
async def test_run_until_day_rejects_non_positive_days() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "run-days-invalid"
    await orchestrator.create_simulation(simulation_id)

    with pytest.raises(ValueError):
        await orchestrator.run_until_day(simulation_id, 0)


@pytest.mark.asyncio
async def test_run_tick_records_script_failure_events() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "failure-record"

    await orchestrator.create_simulation(simulation_id)

    await script_registry.clear()
    try:
        await seed_required_scripts(
            script_registry,
            simulation_id,
            orchestrator=orchestrator,
            skip=[AgentKind.FIRM],
        )

        failing_code = """
def generate_decisions(context):
    raise RuntimeError('boom')
"""

        metadata = await script_registry.register_script(
            simulation_id=simulation_id,
            user_id="firm@failure",
            script_code=failing_code,
            description="firm failure",
            agent_kind=AgentKind.FIRM,
            entity_id="firm_seed",
        )
        await orchestrator.data_access.ensure_entity_state(
            simulation_id,
            metadata.agent_kind,
            metadata.entity_id,
        )

        await orchestrator.run_tick(simulation_id)

        failures = await orchestrator.list_recent_script_failures(
            simulation_id, limit=5
        )
        assert failures, "expected at least one persisted failure"
        failure = failures[0]
        assert failure.script_id == metadata.script_id
        assert "boom" in failure.message
        assert "RuntimeError" in failure.traceback
    finally:
        await script_registry.clear()


@pytest.mark.asyncio
async def test_reset_simulation_restores_initial_state() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "reset-sim"

    await orchestrator.create_simulation(simulation_id)
    await orchestrator.register_participant(simulation_id, "player@example.com")

    await script_registry.clear()
    try:
        await script_registry.register_script(
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

        scripts = await script_registry.list_scripts(simulation_id)
        assert len(scripts) == 1
        assert scripts[0].description == "noop"
        assert scripts[0].code_version
    finally:
        await script_registry.clear()


@pytest.mark.asyncio
async def test_delete_simulation_detaches_associations() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "delete-sim"

    await orchestrator.create_simulation(simulation_id)
    await orchestrator.register_participant(simulation_id, "player@example.com")

    await script_registry.clear()
    try:
        metadata = await script_registry.register_script(
            simulation_id=simulation_id,
            user_id="player@example.com",
            script_code="""
def generate_decisions(context):
    return {}
""",
        )
        assert metadata.code_version

        result = await orchestrator.delete_simulation(simulation_id)

        assert result["participants_removed"] == 1
        assert result["scripts_detached"] == 1

        with pytest.raises(SimulationNotFoundError):
            await orchestrator.get_state(simulation_id)

        assert await script_registry.list_scripts(simulation_id) == []

        # ensure metadata参考保留但已解绑仿真
        all_metadata = await script_registry.list_all_scripts()
        remaining = {m.script_id: m for m in all_metadata}
        assert metadata.script_id in remaining
        assert remaining[metadata.script_id].simulation_id is None
    finally:
        await script_registry.clear()


@pytest.mark.asyncio
async def test_register_script_for_simulation_requires_tick_zero() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "tick-guard-register"

    await orchestrator.create_simulation(simulation_id)

    await script_registry.clear()
    try:
        metadata = await orchestrator.register_script_for_simulation(
            simulation_id=simulation_id,
            user_id="player@example.com",
            script_code="""
def generate_decisions(context):
    return {}
""",
            description="first",
        )
        assert metadata.simulation_id == simulation_id

        await orchestrator.run_tick(simulation_id)

        with pytest.raises(SimulationStateError):
            await orchestrator.register_script_for_simulation(
                simulation_id=simulation_id,
                user_id="player@example.com",
                script_code="""
def generate_decisions(context):
    return {"firm": {"price": 1.0}}
""",
                description="second",
            )
    finally:
        await script_registry.clear()


@pytest.mark.asyncio
async def test_attach_script_to_simulation_requires_tick_zero() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "tick-guard-attach"

    await orchestrator.create_simulation(simulation_id)

    await script_registry.clear()
    try:
        base_script = await script_registry.register_script(
            simulation_id=None,
            user_id="player@example.com",
            script_code="""
def generate_decisions(context):
    return {}
""",
            description="detached",
        )

        attached = await orchestrator.attach_script_to_simulation(
            simulation_id=simulation_id,
            script_id=base_script.script_id,
            user_id="player@example.com",
        )
        assert attached.simulation_id == simulation_id

        await orchestrator.run_tick(simulation_id)

        extra_script = await script_registry.register_script(
            simulation_id=None,
            user_id="player@example.com",
            script_code="""
def generate_decisions(context):
    return {"firm": {"price": 2.0}}
""",
            description="attach-after-run",
        )

        with pytest.raises(SimulationStateError):
            await orchestrator.attach_script_to_simulation(
                simulation_id=simulation_id,
                script_id=extra_script.script_id,
                user_id="player@example.com",
            )
    finally:
        await script_registry.clear()


@pytest.mark.asyncio
async def test_household_baseline_script_executes_successfully() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "baseline-household"

    await orchestrator.create_simulation(simulation_id)

    script_path = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "baseline_scripts"
        / "household_baseline.py"
    )
    script_code = script_path.read_text(encoding="utf-8")

    await script_registry.clear()
    try:
        metadata = await orchestrator.register_script_for_simulation(
            simulation_id=simulation_id,
            user_id="baseline.household@econ.sim",
            script_code=script_code,
            description="baseline household",
        )
        assert metadata.simulation_id == simulation_id

        result = await orchestrator.run_tick(simulation_id)
        assert result.world_state.tick == 1
    finally:
        await script_registry.clear()


@pytest.mark.asyncio
async def test_script_failure_triggers_notifier() -> None:
    await script_registry.clear()
    notifier = StubFailureNotifier()
    orchestrator = SimulationOrchestrator(failure_notifier=notifier)
    simulation_id = "notify-failure"

    await seed_required_scripts(
        script_registry,
        simulation_id,
        orchestrator=orchestrator,
        skip={AgentKind.BANK},
    )

    failing_code = """
def generate_decisions(context):
    raise RuntimeError("bank boom")
"""

    try:
        metadata = await orchestrator.register_script_for_simulation(
            simulation_id=simulation_id,
            user_id="bank.fail@test",
            script_code=failing_code,
            description="failing bank script",
            agent_kind=AgentKind.BANK,
            entity_id="bank_fail",
        )

        result = await orchestrator.run_tick(simulation_id)

        assert result.world_state.bank is not None
        assert notifier.events, "expected failure notifier to capture event"

        event = notifier.events[0]
        assert event.script_id == metadata.script_id
        assert event.user_id == metadata.user_id
        assert event.agent_kind is AgentKind.BANK
        assert event.entity_id == metadata.entity_id
        assert "bank boom" in (event.message + event.traceback)
    finally:
        await script_registry.clear()


@pytest.mark.asyncio
async def test_admin_restrictions_for_simulation_control() -> None:
    await user_manager.reset()
    await script_registry.clear()

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
    await script_registry.clear()

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
        assert "code_version" in payload and payload["code_version"]


@pytest.mark.asyncio
async def test_script_controls_blocked_after_tick_advances() -> None:
    await user_manager.reset()
    await script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={
                "email": "late-user@test.com",
                "password": "StrongPass123",
                "user_type": "individual",
            },
        )
        user_login = await client.post(
            "/auth/login",
            json={"email": "late-user@test.com", "password": "StrongPass123"},
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

        simulation_id = "late-controls"
        created = await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )
        assert created.status_code == 200

        # 推进一次 Tick，使仿真实例进入运行状态
        run_once = await client.post(
            f"/simulations/{simulation_id}/run_tick",
            json={},
            headers=headers_admin,
        )
        assert run_once.status_code == 200

        script_template = """
def generate_decisions(context):
    return {}
"""

        late_upload = await client.post(
            f"/simulations/{simulation_id}/scripts",
            json={"code": script_template},
            headers=headers_user,
        )
        assert late_upload.status_code == 409
        assert "tick" in late_upload.json()["detail"]

        library_upload = await client.post(
            "/scripts",
            json={"code": script_template, "description": "library"},
            headers=headers_user,
        )
        assert library_upload.status_code == 200
        script_id = library_upload.json()["script_id"]

        late_attach = await client.post(
            f"/simulations/{simulation_id}/scripts/attach",
            json={"script_id": script_id},
            headers=headers_user,
        )
        assert late_attach.status_code == 409
        assert "tick" in late_attach.json()["detail"]

        limit_attempt = await client.put(
            f"/simulations/{simulation_id}/settings/script_limit",
            json={"max_scripts_per_user": 2},
            headers=headers_admin,
        )
        assert limit_attempt.status_code == 409
        assert "tick" in limit_attempt.json()["detail"]


@pytest.mark.asyncio
async def test_admin_delete_script_blocked_after_tick_advances() -> None:
    await user_manager.reset()
    await script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={
                "email": "deleter@test.com",
                "password": "StrongPass123",
                "user_type": "individual",
            },
        )
        user_login = await client.post(
            "/auth/login",
            json={"email": "deleter@test.com", "password": "StrongPass123"},
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

        simulation_id = "delete-guard"
        create_resp = await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )
        assert create_resp.status_code == 200

        script_payload = {
            "code": """
def generate_decisions(context):
    return {}
""",
            "description": "attached",
        }

        upload = await client.post(
            f"/simulations/{simulation_id}/scripts",
            json=script_payload,
            headers=headers_user,
        )
        assert upload.status_code == 200
        script_id = upload.json()["script_id"]

        run_once = await client.post(
            f"/simulations/{simulation_id}/run_tick",
            json={},
            headers=headers_admin,
        )
        assert run_once.status_code == 200

        delete_attempt = await client.delete(
            f"/simulations/{simulation_id}/scripts/{script_id}",
            headers=headers_admin,
        )
        assert delete_attempt.status_code == 409
        assert "tick" in delete_attempt.json()["detail"]

        scripts = await script_registry.list_scripts(simulation_id)
        assert any(script.script_id == script_id for script in scripts)

        feature_attempt = await client.put(
            f"/simulations/{simulation_id}/settings/features",
            json={"household_shock_enabled": True},
            headers=headers_admin,
        )
        assert feature_attempt.status_code == 409
        assert "tick" in feature_attempt.json()["detail"]

    await script_registry.clear()


@pytest.mark.asyncio
async def test_admin_can_set_and_get_script_limit() -> None:
    await user_manager.reset()
    await script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_login = await client.post(
            "/auth/login",
            json={
                "email": DEFAULT_ADMIN_EMAIL,
                "password": DEFAULT_ADMIN_PASSWORD,
            },
        )
        admin_token = admin_login.json()["access_token"]
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        simulation_id = "limit-control"
        created = await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )
        assert created.status_code == 200

        applied = await client.put(
            f"/simulations/{simulation_id}/settings/script_limit",
            json={"max_scripts_per_user": 2},
            headers=headers_admin,
        )
        assert applied.status_code == 200
        assert applied.json()["max_scripts_per_user"] == 2

        fetched = await client.get(
            f"/simulations/{simulation_id}/settings/script_limit",
            headers=headers_admin,
        )
        assert fetched.status_code == 200
        assert fetched.json()["max_scripts_per_user"] == 2

        removed = await client.put(
            f"/simulations/{simulation_id}/settings/script_limit",
            json={"max_scripts_per_user": None},
            headers=headers_admin,
        )
        assert removed.status_code == 200
        assert removed.json()["max_scripts_per_user"] is None

        reset_fetch = await client.get(
            f"/simulations/{simulation_id}/settings/script_limit",
            headers=headers_admin,
        )
        assert reset_fetch.status_code == 200
        assert reset_fetch.json()["max_scripts_per_user"] is None

        invalid = await client.put(
            f"/simulations/{simulation_id}/settings/script_limit",
            json={"max_scripts_per_user": 0},
            headers=headers_admin,
        )
        assert invalid.status_code == 422

    await script_registry.clear()


@pytest.mark.asyncio
async def test_admin_can_toggle_features_via_api() -> None:
    await user_manager.reset()
    await script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_login = await client.post(
            "/auth/login",
            json={
                "email": DEFAULT_ADMIN_EMAIL,
                "password": DEFAULT_ADMIN_PASSWORD,
            },
        )
        admin_token = admin_login.json()["access_token"]
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        simulation_id = "feature-toggle"
        created = await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )
        assert created.status_code == 200

        applied = await client.put(
            f"/simulations/{simulation_id}/settings/features",
            json={
                "household_shock_enabled": True,
                "household_shock_ability_std": 0.12,
                "household_shock_asset_std": 0.04,
                "household_shock_max_fraction": 0.25,
            },
            headers=headers_admin,
        )
        assert applied.status_code == 200
        payload = applied.json()
        assert payload["household_shock_enabled"] is True
        assert payload["household_shock_ability_std"] == pytest.approx(0.12)

        fetched = await client.get(
            f"/simulations/{simulation_id}/settings/features",
            headers=headers_admin,
        )
        assert fetched.status_code == 200
        snapshot = fetched.json()
        assert snapshot["household_shock_enabled"] is True
        assert snapshot["household_shock_asset_std"] == pytest.approx(0.04)

    await script_registry.clear()


@pytest.mark.asyncio
async def test_limit_setting_blocks_additional_scripts() -> None:
    await user_manager.reset()
    await script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={
                "email": "limit-user@test.com",
                "password": "StrongPass123",
                "user_type": "individual",
            },
        )
        user_login = await client.post(
            "/auth/login",
            json={"email": "limit-user@test.com", "password": "StrongPass123"},
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

        simulation_id = "limit-enforced"
        await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )

        await client.put(
            f"/simulations/{simulation_id}/settings/script_limit",
            json={"max_scripts_per_user": 1},
            headers=headers_admin,
        )

        script_template = """
def generate_decisions(context):
    return {}
"""

        first = await client.post(
            f"/simulations/{simulation_id}/scripts",
            json={"code": script_template, "description": "first"},
            headers=headers_user,
        )
        assert first.status_code == 200

        second = await client.post(
            f"/simulations/{simulation_id}/scripts",
            json={"code": script_template, "description": "second"},
            headers=headers_user,
        )
        assert second.status_code == 400

    await script_registry.clear()


@pytest.mark.asyncio
async def test_lowering_limit_below_existing_scripts_is_rejected() -> None:
    await user_manager.reset()
    await script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={
                "email": "limit-reduce@test.com",
                "password": "StrongPass123",
                "user_type": "individual",
            },
        )
        user_login = await client.post(
            "/auth/login",
            json={
                "email": "limit-reduce@test.com",
                "password": "StrongPass123",
            },
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

        simulation_id = "limit-reduction"
        await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )

        script_one = """
def generate_decisions(context):
    return {"firm": {"price": 10}}
"""
        script_two = """
def generate_decisions(context):
    return {"firm": {"price": 11}}
"""

        first = await client.post(
            f"/simulations/{simulation_id}/scripts",
            json={"code": script_one},
            headers=headers_user,
        )
        assert first.status_code == 200

        second = await client.post(
            f"/simulations/{simulation_id}/scripts",
            json={"code": script_two},
            headers=headers_user,
        )
        assert second.status_code == 200

        lowered = await client.put(
            f"/simulations/{simulation_id}/settings/script_limit",
            json={"max_scripts_per_user": 1},
            headers=headers_admin,
        )
        assert lowered.status_code == 400

    await script_registry.clear()


@pytest.mark.asyncio
async def test_run_days_endpoint_advances_day() -> None:
    await user_manager.reset()
    await script_registry.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_login = await client.post(
            "/auth/login",
            json={
                "email": DEFAULT_ADMIN_EMAIL,
                "password": DEFAULT_ADMIN_PASSWORD,
            },
        )
        admin_token = admin_login.json()["access_token"]
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        simulation_id = "run-days-endpoint"
        created = await client.post(
            "/simulations",
            json={"simulation_id": simulation_id},
            headers=headers_admin,
        )
        assert created.status_code == 200
        initial_payload = created.json()
        assert initial_payload["current_day"] == 0

        response = await client.post(
            f"/simulations/{simulation_id}/run_days",
            json={"days": 2},
            headers=headers_admin,
        )
        assert response.status_code == 200
    payload = response.json()
    assert payload["days_requested"] == 2
    ticks_per_day = get_world_config().simulation.ticks_per_day
    assert payload["ticks_executed"] == 2 * ticks_per_day
    assert payload["final_day"] >= 2
    assert payload["final_tick"] >= payload["ticks_executed"]


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

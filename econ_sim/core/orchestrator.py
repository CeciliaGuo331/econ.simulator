"""Orchestrator: bring back the full async SimulationOrchestrator from the
backup implementation but wire it to the new modular market logic.

This module exposes the same public class names and exceptions used by the
API layer while delegating market execution to the `econ_sim.logic_modules`
subsystems (coupon, labor, goods, bond, central bank, transfers).
"""

from __future__ import annotations

import asyncio
import math
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, TYPE_CHECKING, Any

from ..data_access.models import (
    AgentKind,
    HouseholdShock,
    StateUpdateCommand,
    TickDecisionOverrides,
    TickDecisions,
    TickResult,
    TickLogEntry,
    SimulationFeatures,
    ScriptFailureRecord,
    WorldState,
)
from ..data_access.redis_client import DataAccessLayer, SimulationNotFoundError
from ..core.fallback_manager import BaselineFallbackManager, FallbackExecutionError
from ..logic_modules.agent_logic import collect_tick_decisions, merge_tick_overrides
from ..utils.settings import get_world_config
from ..script_engine import script_registry
from ..script_engine.notifications import (
    LoggingScriptFailureNotifier,
    ScriptFailureNotifier,
)
from ..script_engine.registry import ScriptExecutionError, ScriptFailureEvent
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - only for type checking to avoid runtime cycle
    from ..script_engine.registry import ScriptMetadata

logger = logging.getLogger(__name__)


@dataclass
class BatchRunResult:
    """Batch execution result wrapper."""

    world_state: WorldState
    ticks_executed: int
    logs: List[TickLogEntry]


class SimulationStateError(RuntimeError):
    def __init__(self, simulation_id: str, tick: int) -> None:
        super().__init__(
            f"Simulation {simulation_id} is at tick {tick}; operation requires tick 0."
        )
        self.simulation_id = simulation_id
        self.tick = tick


class MissingAgentScriptsError(RuntimeError):
    def __init__(self, simulation_id: str, missing_agents: Iterable[AgentKind]) -> None:
        missing_list = ", ".join(sorted(agent.value for agent in missing_agents))
        super().__init__(
            f"Simulation {simulation_id} is missing required scripts for: {missing_list}"
        )
        self.simulation_id = simulation_id
        self.missing_agents = tuple(missing_agents)


class DayBoundaryRequiredError(RuntimeError):
    def __init__(self, simulation_id: str, tick: int, ticks_per_day: int) -> None:
        super().__init__(
            f"Simulation {simulation_id} at tick {tick} is not at day boundary (ticks_per_day={ticks_per_day})."
        )
        self.simulation_id = simulation_id
        self.tick = tick
        self.ticks_per_day = ticks_per_day


class SimulationOrchestrator:
    """Main simulation orchestrator wired to new modular logic modules."""

    def __init__(
        self,
        data_access: Optional[DataAccessLayer] = None,
        *,
        failure_notifier: Optional[ScriptFailureNotifier] = None,
    ) -> None:
        config = get_world_config()
        self.data_access = data_access or DataAccessLayer.with_default_store(config)
        self.config = self.data_access.config
        self._tick_logs: Dict[str, List[TickLogEntry]] = {}
        self._required_agents: tuple[AgentKind, ...] = (
            AgentKind.HOUSEHOLD,
            AgentKind.FIRM,
            AgentKind.BANK,
            AgentKind.GOVERNMENT,
            AgentKind.CENTRAL_BANK,
        )
        self._fallback_manager = BaselineFallbackManager()
        self._failure_notifier = (
            failure_notifier
            if failure_notifier is not None
            else LoggingScriptFailureNotifier()
        )
        self._recent_phase_timings = deque(maxlen=200)

    async def create_simulation(self, simulation_id: str) -> WorldState:
        return await self.data_access.ensure_simulation(simulation_id)

    async def register_participant(self, simulation_id: str, user_id: str) -> list[str]:
        await self.data_access.get_world_state(simulation_id)
        await self.data_access.register_participant(simulation_id, user_id)
        return await self.data_access.list_participants(simulation_id)

    async def list_participants(self, simulation_id: str) -> list[str]:
        await self.data_access.get_world_state(simulation_id)
        return await self.data_access.list_participants(simulation_id)

    async def register_script_for_simulation(
        self,
        simulation_id: str,
        user_id: str,
        script_code: str,
        description: Optional[str] = None,
        *,
        agent_kind: AgentKind,
        entity_id: Optional[str] = None,
    ) -> "ScriptMetadata":
        await self._require_tick_zero(simulation_id)
        resolved_entity_id = entity_id
        if resolved_entity_id is None:
            resolved_entity_id = await self._allocate_entity_id(
                simulation_id, agent_kind
            )
        metadata = await script_registry.register_script(
            simulation_id=simulation_id,
            user_id=user_id,
            script_code=script_code,
            description=description,
            agent_kind=agent_kind,
            entity_id=resolved_entity_id,
        )
        await self._ensure_entity_seeded(metadata)
        return metadata

    async def set_script_limit(
        self, simulation_id: str, limit: Optional[int]
    ) -> Optional[int]:
        if limit is not None and limit <= 0:
            raise ValueError("script limit must be positive or null")
        await self._require_tick_zero(simulation_id)
        normalized_limit = int(limit) if limit is not None else None
        if normalized_limit is not None:
            scripts = await script_registry.list_scripts(simulation_id)
            user_counts = Counter(meta.user_id for meta in scripts)
            exceeding = [
                user for user, count in user_counts.items() if count > normalized_limit
            ]
            if exceeding:
                raise ValueError(
                    "Existing scripts exceed the requested limit for users: "
                    + ", ".join(sorted(set(exceeding)))
                )
        return await script_registry.set_simulation_limit(
            simulation_id, normalized_limit
        )

    async def get_script_limit(self, simulation_id: str) -> Optional[int]:
        await self.data_access.get_world_state(simulation_id)
        return await script_registry.get_simulation_limit(simulation_id)

    async def list_simulations(self) -> list[str]:
        return await self.data_access.list_simulations()

    async def attach_script_to_simulation(
        self, simulation_id: str, script_id: str, user_id: str
    ) -> "ScriptMetadata":
        await self._require_tick_zero(simulation_id)
        metadata_before = await script_registry.get_user_script(script_id, user_id)
        resolved_entity_id = metadata_before.entity_id
        if script_registry.is_placeholder_entity_id(resolved_entity_id):
            resolved_entity_id = await self._allocate_entity_id(
                simulation_id, metadata_before.agent_kind
            )
        metadata = await script_registry.attach_script(
            script_id=script_id,
            simulation_id=simulation_id,
            user_id=user_id,
            entity_id=resolved_entity_id,
        )
        await self._ensure_entity_seeded(metadata)
        return metadata

    async def remove_script_from_simulation(
        self, simulation_id: str, script_id: str
    ) -> None:
        await self._require_tick_zero(simulation_id)
        scripts = await script_registry.list_scripts(simulation_id)
        target = next((meta for meta in scripts if meta.script_id == script_id), None)
        if target is None:
            raise ScriptExecutionError("Script not found for simulation")
        await script_registry.remove_script(simulation_id, script_id)
        await self.data_access.remove_entity_state(
            simulation_id, target.agent_kind, target.entity_id
        )

    async def detach_script_from_simulation(
        self, simulation_id: str, script_id: str, user_id: str
    ) -> "ScriptMetadata":
        await self._require_tick_zero(simulation_id)
        metadata = await script_registry.get_user_script(script_id, user_id)
        if metadata.simulation_id != simulation_id:
            raise ScriptExecutionError("脚本未挂载到指定仿真实例。")
        updated = await script_registry.detach_user_script(script_id, user_id)
        await self.data_access.remove_entity_state(
            simulation_id, metadata.agent_kind, metadata.entity_id
        )
        return updated

    async def get_state(self, simulation_id: str) -> WorldState:
        return await self.data_access.get_world_state(simulation_id)

    async def get_recent_logs(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> List[TickLogEntry]:
        await self.data_access.get_world_state(simulation_id)
        return await self.data_access.get_recent_logs(simulation_id, limit)

    async def list_recent_script_failures(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> List[ScriptFailureRecord]:
        await self.data_access.get_world_state(simulation_id)
        return await self.data_access.list_script_failures(simulation_id, limit)

    async def run_tick(
        self, simulation_id: str, overrides: Optional[TickDecisionOverrides] = None
    ) -> TickResult:
        # load world and ensure agent coverage
        world_state = await self.create_simulation(simulation_id)
        await self._require_agent_coverage(simulation_id)

        shocks: Dict[int, HouseholdShock] = {}
        decision_state = world_state
        if world_state.features.household_shock_enabled:
            from ..logic_modules.shock_logic import (
                generate_household_shocks,
                apply_household_shocks_for_decision,
            )

            shocks = generate_household_shocks(world_state, self.config)
            decision_state = apply_household_shocks_for_decision(world_state, shocks)

        # baseline decisions
        baseline_dur = None
        start = time.perf_counter()
        try:
            baseline_decisions = self._fallback_manager.generate_decisions(
                decision_state, self.config
            )
        except FallbackExecutionError as exc:
            end = time.perf_counter()
            baseline_dur = end - start
            logger.exception(
                "Failed to generate fallback decisions for simulation %s", simulation_id
            )
            raise RuntimeError(
                "Baseline fallback failed to produce required decisions"
            ) from exc
        else:
            end = time.perf_counter()
            baseline_dur = end - start

        # script overrides
        start = time.perf_counter()
        (script_overrides, script_failure_logs, failure_events) = (
            await script_registry.generate_overrides(
                simulation_id, decision_state, self.config
            )
        )
        end = time.perf_counter()
        overrides_dur = end - start
        try:
            from ..script_engine.sandbox import get_sandbox_metrics

            metrics = get_sandbox_metrics()
            logger.debug("Sandbox metrics after generate_overrides: %s", metrics)
        except Exception:
            logger.debug("Failed to collect sandbox metrics")

        self._dispatch_script_failures(failure_events)
        if failure_events:
            await self.data_access.record_script_failures(failure_events)

        combined_overrides = merge_tick_overrides(script_overrides, overrides)

        # collect final decisions
        start = time.perf_counter()
        decisions = await asyncio.to_thread(
            collect_tick_decisions, baseline_decisions, combined_overrides
        )
        end = time.perf_counter()
        collect_dur = end - start

        # execute market logic using new modular subsystems
        start = time.perf_counter()
        updates, logs, ledgers, market_signals = await asyncio.to_thread(
            _execute_market_logic, world_state, decisions, self.config, shocks
        )
        end = time.perf_counter()
        execute_dur = end - start

        if script_failure_logs:
            logs = script_failure_logs + logs

        if world_state.features.household_shock_enabled and shocks:
            updates.append(
                StateUpdateCommand.assign(
                    AgentKind.WORLD,
                    agent_id=None,
                    household_shocks={
                        hid: shock.model_dump() for hid, shock in shocks.items()
                    },
                )
            )
        elif world_state.household_shocks:
            updates.append(
                StateUpdateCommand.assign(
                    AgentKind.WORLD, agent_id=None, household_shocks={}
                )
            )

        next_tick = world_state.tick + 1
        sim_config = self.config.simulation
        ticks_since_start = next_tick - sim_config.initial_tick
        if ticks_since_start <= 0:
            next_day = sim_config.initial_day
        else:
            next_day = sim_config.initial_day + math.ceil(
                ticks_since_start / sim_config.ticks_per_day
            )
        next_day = max(next_day, world_state.day)

        updates.append(
            StateUpdateCommand.assign(
                AgentKind.WORLD, agent_id=None, tick=next_tick, day=next_day
            )
        )

        # persist updates and record tick
        start = time.perf_counter()
        updated_state = await self.data_access.apply_updates(simulation_id, updates)
        tick_result = TickResult(
            world_state=updated_state,
            logs=logs,
            updates=updates,
            market_signals=market_signals,
            ledgers=ledgers,
        )
        await self.data_access.record_tick(tick_result)
        end = time.perf_counter()
        persist_dur = end - start

        try:
            current_tick = world_state.tick
            self._recent_phase_timings.append(
                {
                    "simulation_id": simulation_id,
                    "tick": current_tick,
                    "baseline": baseline_dur,
                    "generate_overrides": overrides_dur,
                    "collect_decisions": collect_dur,
                    "execute_logic": execute_dur,
                    "persist": persist_dur,
                }
            )
        except Exception:
            logger.debug(
                "Failed to append phase timings for simulation %s", simulation_id
            )

        return tick_result

    async def reset_simulation(self, simulation_id: str) -> WorldState:
        previous_features: Optional[SimulationFeatures] = None
        try:
            current = await self.data_access.get_world_state(simulation_id)
            previous_features = current.features.model_copy(deep=True)
        except SimulationNotFoundError:
            previous_features = None

        state = await self.data_access.reset_simulation(simulation_id)
        self._tick_logs[simulation_id] = []

        if previous_features is not None:
            state = await self.update_simulation_features(
                simulation_id, **previous_features.model_dump()
            )

        try:
            if hasattr(script_registry, "_ensure_simulation_loaded"):
                try:
                    await script_registry._ensure_simulation_loaded(simulation_id)
                except Exception:
                    logger.debug(
                        "_ensure_simulation_loaded failed for %s, continuing to list_scripts",
                        simulation_id,
                    )

            scripts = await script_registry.list_scripts(simulation_id)
            for meta in scripts:
                await self._ensure_entity_seeded(meta)
        except Exception:
            logger.exception(
                "Failed to re-seed entities from scripts after resetting %s",
                simulation_id,
            )

        return state

    async def get_simulation_features(self, simulation_id: str) -> SimulationFeatures:
        state = await self.data_access.get_world_state(simulation_id)
        return state.features

    async def update_simulation_features(
        self, simulation_id: str, **updates: object
    ) -> WorldState:
        state = await self._require_tick_zero(simulation_id)
        features = state.features.model_copy(deep=True)
        allowed_fields = set(features.model_dump().keys())
        mutated = False
        for field, value in updates.items():
            if field not in allowed_fields or value is None:
                continue
            setattr(features, field, value)
            mutated = True
        if not mutated:
            return state
        updated = await self.data_access.apply_updates(
            simulation_id,
            [
                StateUpdateCommand.assign(
                    AgentKind.WORLD, agent_id=None, features=features.model_dump()
                )
            ],
        )
        if not features.household_shock_enabled:
            updated = await self.data_access.apply_updates(
                simulation_id,
                [
                    StateUpdateCommand.assign(
                        AgentKind.WORLD, agent_id=None, household_shocks={}
                    )
                ],
            )
        return updated

    async def run_until_day(self, simulation_id: str, days: int) -> BatchRunResult:
        if days <= 0:
            raise ValueError("days must be a positive integer")
        state = await self.create_simulation(simulation_id)
        ticks_per_day = self.config.simulation.ticks_per_day
        target_tick = state.tick + days * ticks_per_day
        ticks_executed = 0
        aggregated_logs: List[TickLogEntry] = []
        last_result: Optional[TickResult] = None
        safety_limit = max(days * ticks_per_day * 5, ticks_per_day * 2)
        while True:
            if state.tick >= target_tick:
                break
            last_result = await self.run_tick(simulation_id)
            state = last_result.world_state
            ticks_executed += 1
            aggregated_logs.extend(last_result.logs)
            await asyncio.sleep(0)
            if ticks_executed > safety_limit:
                raise RuntimeError(
                    "Exceeded expected number of ticks while advancing simulation"
                )
        return BatchRunResult(
            world_state=state, ticks_executed=ticks_executed, logs=aggregated_logs
        )

    async def run_day(
        self, simulation_id: str, *, ticks_per_day: Optional[int] = None
    ) -> BatchRunResult:
        state = await self.create_simulation(simulation_id)
        configured_ticks = self.config.simulation.ticks_per_day
        ticks_to_run = configured_ticks if ticks_per_day is None else ticks_per_day
        if ticks_to_run <= 0:
            raise ValueError("ticks_per_day must be a positive integer")
        aggregated_logs: List[TickLogEntry] = []
        ticks_executed = 0
        last_result: Optional[TickResult] = None
        for _ in range(ticks_to_run):
            last_result = await self.run_tick(simulation_id)
            state = last_result.world_state
            aggregated_logs.extend(last_result.logs)
            ticks_executed += 1
        if last_result is None:
            return BatchRunResult(world_state=state, ticks_executed=0, logs=[])
        return BatchRunResult(
            world_state=state, ticks_executed=ticks_executed, logs=aggregated_logs
        )

    async def delete_simulation(self, simulation_id: str) -> dict[str, int]:
        participants_removed = await self.data_access.delete_simulation(simulation_id)
        scripts_removed = await script_registry.detach_simulation(simulation_id)
        return {
            "participants_removed": participants_removed,
            "scripts_detached": scripts_removed,
        }

    async def update_script_code_at_day_end(
        self,
        simulation_id: str,
        *,
        script_id: str,
        user_id: Optional[str],
        new_code: str,
        new_description: Optional[str] = None,
    ) -> "ScriptMetadata":
        state = await self.data_access.get_world_state(simulation_id)
        ticks_per_day = self.config.simulation.ticks_per_day
        if ticks_per_day <= 0:
            raise ValueError("ticks_per_day must be positive in config")
        if state.tick % ticks_per_day != 0:
            raise DayBoundaryRequiredError(simulation_id, state.tick, ticks_per_day)
        scripts = await script_registry.list_scripts(simulation_id)
        target = next((m for m in scripts if m.script_id == script_id), None)
        if target is None:
            raise ScriptExecutionError("Script not found for simulation")
        updated = await script_registry.update_script_code(
            script_id=script_id,
            user_id=user_id,
            new_code=new_code,
            new_description=new_description,
        )
        return updated

    async def _require_agent_coverage(self, simulation_id: str) -> None:
        state = await self.data_access.get_world_state(simulation_id)
        present = set()
        if state.households:
            present.add(AgentKind.HOUSEHOLD)
        if state.firm is not None:
            present.add(AgentKind.FIRM)
        if state.bank is not None:
            present.add(AgentKind.BANK)
        if state.government is not None:
            present.add(AgentKind.GOVERNMENT)
        if state.central_bank is not None:
            present.add(AgentKind.CENTRAL_BANK)
        scripts = await script_registry.list_scripts(simulation_id)
        scripted_kinds = {meta.agent_kind for meta in scripts}
        missing: list[AgentKind] = []
        for agent in self._required_agents:
            if agent not in present or agent not in scripted_kinds:
                missing.append(agent)
        if AgentKind.HOUSEHOLD not in missing and state.households:
            scripted_households = {
                meta.entity_id
                for meta in scripts
                if meta.agent_kind is AgentKind.HOUSEHOLD
            }
            expected_households = {
                str(identifier) for identifier in state.households.keys()
            }
            if not expected_households.issubset(scripted_households):
                missing.append(AgentKind.HOUSEHOLD)
        if missing:
            raise MissingAgentScriptsError(simulation_id, missing)

    async def _require_tick_zero(self, simulation_id: str) -> WorldState:
        state = await self.data_access.get_world_state(simulation_id)
        if state.tick != 0:
            raise SimulationStateError(simulation_id, state.tick)
        return state

    async def _allocate_entity_id(
        self, simulation_id: str, agent_kind: AgentKind
    ) -> str:
        state = await self.data_access.get_world_state(simulation_id)
        if agent_kind is AgentKind.HOUSEHOLD:
            used_ids = {int(identifier) for identifier in state.households.keys()}
            scripts = await script_registry.list_scripts(simulation_id)
            for metadata in scripts:
                if (
                    metadata.agent_kind is AgentKind.HOUSEHOLD
                    and metadata.entity_id.isdigit()
                ):
                    used_ids.add(int(metadata.entity_id))
            candidate = 1
            while candidate in used_ids:
                candidate += 1
            return str(candidate)
        if agent_kind is AgentKind.FIRM:
            if state.firm is not None and state.firm.id:
                return state.firm.id
            return "firm_1"
        if agent_kind is AgentKind.BANK:
            if state.bank is not None and state.bank.id:
                return state.bank.id
            return "bank"
        if agent_kind is AgentKind.GOVERNMENT:
            if state.government is not None and state.government.id:
                return state.government.id
            return "government"
        if agent_kind is AgentKind.CENTRAL_BANK:
            if state.central_bank is not None and state.central_bank.id:
                return state.central_bank.id
            return "central_bank"
        raise ScriptExecutionError(f"无法为主体类型 {agent_kind.value} 自动生成实体 ID")

    async def _ensure_entity_seeded(self, metadata: "ScriptMetadata") -> None:
        if metadata.simulation_id is None:
            return
        await self.data_access.ensure_entity_state(
            metadata.simulation_id, metadata.agent_kind, metadata.entity_id
        )

    def _dispatch_script_failures(self, events: List[ScriptFailureEvent]) -> None:
        if not events:
            return
        for event in events:
            try:
                self._failure_notifier.notify(event)
            except Exception:
                logger.exception(
                    "Failed to dispatch script failure notification for %s",
                    event.script_id,
                )


def _execute_market_logic(
    world_state: WorldState,
    decisions: TickDecisions,
    config: Any,
    shocks: Dict[int, HouseholdShock],
):
    """Run the modular market subsystems in order and return updates/logs/ledgers/signals.

    This function is CPU-bound-light and safe to run in a thread using asyncio.to_thread.
    """
    from ..data_access.models import StateUpdateCommand, TickLogEntry

    updates: List[StateUpdateCommand] = []
    logs: List[TickLogEntry] = []
    ledgers: List[Any] = []
    market_signals: Dict[str, Any] = {}

    tick = world_state.tick
    day = world_state.day

    # 1) coupon payments
    try:
        from ..logic_modules import government_financial

        c_updates, c_ledgers, c_log = government_financial.process_coupon_payments(
            world_state, tick=tick, day=day
        )
        updates.extend(c_updates)
        ledgers.extend(c_ledgers)
        logs.append(c_log)
    except Exception:
        pass

    # 2) labor market
    try:
        from ..logic_modules import labor_market

        l_updates, l_log = labor_market.resolve_labor_market_new(world_state, decisions)
        updates.extend(l_updates)
        logs.append(l_log)
    except Exception:
        pass

    # 3) wages settlement (best-effort)
    try:
        firm = getattr(world_state, "firm", None)
        government = getattr(world_state, "government", None)
        wage_updates: List[StateUpdateCommand] = []
        firm_payroll = 0.0
        gov_payroll = 0.0

        if firm is not None:
            for hid in getattr(firm, "employees", []):
                try:
                    hh = world_state.households[hid]
                    wage = float(decisions.firm.wage_offer)
                    hh.balance_sheet.cash = float(hh.balance_sheet.cash) + wage
                    firm_payroll += wage
                    wage_updates.append(
                        StateUpdateCommand.assign(
                            scope=AgentKind.HOUSEHOLD,
                            agent_id=hid,
                            balance_sheet=hh.balance_sheet.model_dump(),
                            wage_income=wage,
                        )
                    )
                except Exception:
                    continue
            if firm_payroll > 0:
                try:
                    firm.balance_sheet.cash = (
                        float(firm.balance_sheet.cash) - firm_payroll
                    )
                except Exception:
                    pass
                wage_updates.append(
                    StateUpdateCommand.assign(
                        scope=AgentKind.FIRM,
                        agent_id=firm.id,
                        balance_sheet=firm.balance_sheet.model_dump(),
                    )
                )

        if government is not None:
            for hid in getattr(government, "employees", []):
                try:
                    hh = world_state.households[hid]
                    wage = float(decisions.firm.wage_offer * 0.8)
                    hh.balance_sheet.cash = float(hh.balance_sheet.cash) + wage
                    gov_payroll += wage
                    wage_updates.append(
                        StateUpdateCommand.assign(
                            scope=AgentKind.HOUSEHOLD,
                            agent_id=hid,
                            balance_sheet=hh.balance_sheet.model_dump(),
                            wage_income=wage,
                        )
                    )
                except Exception:
                    continue
            if gov_payroll > 0:
                try:
                    government.balance_sheet.cash = (
                        float(government.balance_sheet.cash) - gov_payroll
                    )
                except Exception:
                    pass
                wage_updates.append(
                    StateUpdateCommand.assign(
                        scope=AgentKind.GOVERNMENT,
                        agent_id=government.id,
                        balance_sheet=government.balance_sheet.model_dump(),
                    )
                )

        if wage_updates:
            updates.extend(wage_updates)
            logs.append(
                TickLogEntry(
                    tick=tick,
                    day=day,
                    message="wages_disbursed",
                    context={
                        "firm_payroll": float(firm_payroll),
                        "government_payroll": float(gov_payroll),
                    },
                )
            )
    except Exception:
        pass

    # 4) goods market
    try:
        from ..logic_modules import goods_market

        g_updates, g_log = goods_market.clear_goods_market_new(world_state, decisions)
        updates.extend(g_updates)
        logs.append(g_log)
    except Exception:
        pass

    # 5) government transfers
    try:
        from ..logic_modules import government_transfers

        u_updates, u_ledgers, u_log = government_transfers.unemployment_benefit(
            world_state,
            decisions.government,
            bids=getattr(decisions, "bond_bids", None),
        )
        m_updates, m_ledgers, m_log = government_transfers.means_tested_transfer(
            world_state,
            decisions.government,
            bids=getattr(decisions, "bond_bids", None),
        )
        updates.extend(u_updates)
        updates.extend(m_updates)
        ledgers.extend(u_ledgers)
        ledgers.extend(m_ledgers)
        logs.append(u_log)
        logs.append(m_log)
    except Exception:
        pass

    # 6) central bank OMO
    try:
        from ..logic_modules import central_bank

        omo_ops = getattr(decisions.central_bank, "omo_ops", [])
        cb_updates, cb_ledgers, cb_log = central_bank.process_omo(
            world_state, tick=tick, day=day, omo_ops=omo_ops
        )
        updates.extend(cb_updates)
        ledgers.extend(cb_ledgers)
        logs.append(cb_log)
    except Exception:
        pass

    # 7) bond maturities
    try:
        from ..logic_modules import government_financial

        mat_updates, mat_ledgers, mat_log = (
            government_financial.process_bond_maturities(
                world_state, tick=tick, day=day
            )
        )
        updates.extend(mat_updates)
        ledgers.extend(mat_ledgers)
        logs.append(mat_log)
    except Exception:
        pass

    # collect market signals
    try:
        by = getattr(world_state.macro, "bond_yield", None)
        if by is not None:
            market_signals["bond_yield"] = float(by)
    except Exception:
        pass

    return updates, logs, ledgers, market_signals


__all__ = [
    "SimulationOrchestrator",
    "SimulationNotFoundError",
    "SimulationStateError",
    "BatchRunResult",
]


def run_tick_new(world_state: WorldState):
    """Compatibility helper used by tests and tooling: run a single tick locally
    using the modular market subsystems and a fallback baseline if needed.
    Returns (updates, logs, ledgers, market_signals).
    """
    try:
        from ..logic_modules import baseline_stub

        decisions = baseline_stub.generate_baseline_decisions(world_state)
    except Exception:
        fb = BaselineFallbackManager()
        decisions = fb.generate_decisions(world_state, get_world_config())

    updates, logs, ledgers, market_signals = _execute_market_logic(
        world_state, decisions, get_world_config(), {}
    )
    return updates, logs, ledgers, market_signals

"""使用内置基线脚本生成默认决策（fallback）。

该管理器为每类主体调用部署目录下的 baseline 脚本，用于在用户脚本缺失时
提供合理的默认决策。脚本返回结构应遵循约定（见代码中的调用处）。
"""

from __future__ import annotations

from importlib import import_module
from typing import Callable, Dict

from ..data_access.models import (
    AgentKind,
    BankDecision,
    CentralBankDecision,
    FirmDecision,
    GovernmentDecision,
    HouseholdDecision,
    TickDecisions,
    WorldState,
)
from ..utils.settings import WorldConfig


class FallbackExecutionError(RuntimeError):
    """当基线回退为任一实体生成决策失败时抛出。"""

    def __init__(
        self, agent_kind: AgentKind, entity_id: str | int, reason: str
    ) -> None:
        message = (
            "Baseline fallback failed for "
            f"agent_kind={agent_kind.value}, entity_id={entity_id}: {reason}"
        )
        super().__init__(message)
        self.agent_kind = agent_kind
        self.entity_id = entity_id
        self.reason = reason


class BaselineFallbackManager:
    """执行内置基线脚本以生成 fallback 决策的管理类。

    它按实体类型加载相应模块并调用约定的 generate_decisions(context) 接口，
    将返回值转换为项目内部用于后续市场逻辑的决策模型对象。
    """

    _HOUSEHOLD_MODULE = "deploy.baseline_scripts.household_baseline"
    _AGENT_MODULES: Dict[AgentKind, str] = {
        AgentKind.FIRM: "deploy.baseline_scripts.firm_baseline",
        AgentKind.BANK: "deploy.baseline_scripts.bank_baseline",
        AgentKind.GOVERNMENT: "deploy.baseline_scripts.government_baseline",
        AgentKind.CENTRAL_BANK: "deploy.baseline_scripts.central_bank_baseline",
    }

    def __init__(self) -> None:
        self._cache: Dict[str, Callable[[dict[str, object]], dict[str, object]]] = {}

    def generate_decisions(  # noqa: D401 - short description handled by class docstring
        self,
        world_state: WorldState,
        config: WorldConfig,
    ) -> TickDecisions:
        context_base = {
            "world_state": world_state.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
            "script_api_version": 1,
        }

        households: Dict[int, HouseholdDecision] = {}
        for household_id, household_state in world_state.households.items():
            context = {
                **context_base,
                "agent_kind": AgentKind.HOUSEHOLD.value,
                "entity_id": household_id,
                "entity_state": household_state.model_dump(mode="json"),
            }
            raw = self._execute(self._HOUSEHOLD_MODULE, context)
            payload = raw.get("households") if isinstance(raw, dict) else None
            candidate = None
            if isinstance(payload, dict):
                candidate = payload.get(household_id)
                if candidate is None:
                    candidate = payload.get(str(household_id))
            if not candidate:
                raise FallbackExecutionError(
                    AgentKind.HOUSEHOLD,
                    household_id,
                    "baseline script returned no decision",
                )
            households[household_id] = HouseholdDecision.model_validate(candidate)

        firm_state = world_state.firm
        if firm_state is None:
            raise FallbackExecutionError(
                AgentKind.FIRM,
                "<missing>",
                "firm entity not present in world state",
            )
        firm_decision = self._run_singleton_agent(
            AgentKind.FIRM,
            firm_state.id,
            firm_state.model_dump(mode="json"),
            context_base,
        )

        bank_state = world_state.bank
        if bank_state is None:
            raise FallbackExecutionError(
                AgentKind.BANK,
                "<missing>",
                "bank entity not present in world state",
            )
        bank_decision = self._run_singleton_agent(
            AgentKind.BANK,
            bank_state.id,
            bank_state.model_dump(mode="json"),
            context_base,
        )

        government_state = world_state.government
        if government_state is None:
            raise FallbackExecutionError(
                AgentKind.GOVERNMENT,
                "<missing>",
                "government entity not present in world state",
            )
        government_decision = self._run_singleton_agent(
            AgentKind.GOVERNMENT,
            government_state.id,
            government_state.model_dump(mode="json"),
            context_base,
        )

        central_bank_state = world_state.central_bank
        if central_bank_state is None:
            raise FallbackExecutionError(
                AgentKind.CENTRAL_BANK,
                "<missing>",
                "central bank entity not present in world state",
            )
        central_bank_decision = self._run_singleton_agent(
            AgentKind.CENTRAL_BANK,
            central_bank_state.id,
            central_bank_state.model_dump(mode="json"),
            context_base,
        )

        return TickDecisions(
            households=households,
            firm=firm_decision,
            bank=bank_decision,
            government=government_decision,
            central_bank=central_bank_decision,
        )

    def _run_singleton_agent(
        self,
        agent_kind: AgentKind,
        entity_id: str,
        entity_state: dict[str, object],
        context_base: dict[str, object],
    ) -> FirmDecision | BankDecision | GovernmentDecision | CentralBankDecision:
        module_path = self._AGENT_MODULES.get(agent_kind)
        if module_path is None:
            raise FallbackExecutionError(
                agent_kind,
                entity_id,
                "no baseline module configured",
            )

        context = {
            **context_base,
            "agent_kind": agent_kind.value,
            "entity_id": entity_id,
            "entity_state": entity_state,
        }
        raw = self._execute(module_path, context)
        if not isinstance(raw, dict):
            raise FallbackExecutionError(
                agent_kind,
                entity_id,
                "baseline script returned invalid payload",
            )

        key = agent_kind.value
        payload = raw.get(key)
        if not isinstance(payload, dict):
            raise FallbackExecutionError(
                agent_kind,
                entity_id,
                "baseline script returned no decision",
            )

        if agent_kind is AgentKind.FIRM:
            return FirmDecision.model_validate(payload)
        if agent_kind is AgentKind.BANK:
            return BankDecision.model_validate(payload)
        if agent_kind is AgentKind.GOVERNMENT:
            return GovernmentDecision.model_validate(payload)
        if agent_kind is AgentKind.CENTRAL_BANK:
            return CentralBankDecision.model_validate(payload)

        raise FallbackExecutionError(
            agent_kind,
            entity_id,
            "unsupported agent kind for singleton fallback",
        )

    def _execute(
        self,
        module_path: str,
        context: dict[str, object],
    ) -> dict[str, object]:
        generator = self._load_generator(module_path)
        result = generator(context)
        if result is None:
            return {}
        if not isinstance(result, dict):
            agent_value = str(context.get("agent_kind", AgentKind.WORLD.value))
            try:
                agent_kind = AgentKind(agent_value)
            except ValueError:
                agent_kind = AgentKind.WORLD
            raise FallbackExecutionError(
                agent_kind,
                context.get("entity_id", "unknown"),
                "baseline script must return a dictionary",
            )
        return result

    def _load_generator(
        self, module_path: str
    ) -> Callable[[dict[str, object]], dict[str, object]]:
        if module_path in self._cache:
            return self._cache[module_path]
        module = import_module(module_path)
        generator = getattr(module, "generate_decisions", None)
        if generator is None:
            raise RuntimeError(
                f"Baseline module {module_path} has no generate_decisions function"
            )
        if not callable(generator):
            raise RuntimeError(
                f"Baseline module {module_path}.generate_decisions is not callable"
            )
        self._cache[module_path] = generator
        return generator


__all__ = ["BaselineFallbackManager", "FallbackExecutionError"]

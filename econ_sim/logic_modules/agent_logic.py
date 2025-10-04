"""Agent decision orchestration built on top of base strategies."""

from __future__ import annotations

from typing import Dict, Optional

from ..data_access.models import (
    BankDecision,
    CentralBankDecision,
    FirmDecision,
    GovernmentDecision,
    HouseholdDecision,
    TickDecisionOverrides,
    TickDecisions,
    WorldState,
)
from ..strategies.base import StrategyBundle


def _apply_override(default_decision, override) -> object:
    if override is None:
        return default_decision
    updates = {
        k: v
        for k, v in override.model_dump(exclude_unset=True).items()
        if v is not None
    }
    if not updates:
        return default_decision
    return default_decision.model_copy(update=updates)


def collect_tick_decisions(
    world_state: WorldState,
    strategies: StrategyBundle,
    overrides: Optional[TickDecisionOverrides] = None,
) -> TickDecisions:
    """Generate tick-level decisions for all agents.

    Parameters
    ----------
    world_state:
        Current world snapshot.
    strategies:
        Strategy bundle providing default heuristics.
    overrides:
        Optional player supplied decisions overriding the defaults.
    """

    public_data = world_state.get_public_market_data()

    override_households = overrides.households if overrides else {}
    household_decisions: Dict[int, HouseholdDecision] = {}
    for household_id, household_state in world_state.households.items():
        default = strategies.household_strategy(household_id).decide(
            household_state, public_data
        )
        override = override_households.get(household_id)
        household_decisions[household_id] = _apply_override(default, override)

    default_firm = strategies.firm.decide(world_state.firm, world_state)
    firm_override = overrides.firm if overrides else None
    firm_decision: FirmDecision = _apply_override(default_firm, firm_override)

    default_government = strategies.government.decide(
        world_state.government, world_state.macro.unemployment_rate
    )
    government_override = overrides.government if overrides else None
    government_decision: GovernmentDecision = _apply_override(
        default_government, government_override
    )

    default_bank = strategies.bank.decide(world_state.bank, world_state.central_bank)
    bank_override = overrides.bank if overrides else None
    bank_decision: BankDecision = _apply_override(default_bank, bank_override)

    default_central_bank = strategies.central_bank.decide(
        world_state.central_bank, public_data
    )
    central_bank_override = overrides.central_bank if overrides else None
    central_bank_decision: CentralBankDecision = _apply_override(
        default_central_bank, central_bank_override
    )

    return TickDecisions(
        households=household_decisions,
        firm=firm_decision,
        bank=bank_decision,
        government=government_decision,
        central_bank=central_bank_decision,
    )

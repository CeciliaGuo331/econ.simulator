"""Compatibility shim: provide minimal helper used by script registry.

This repository previously exposed `merge_tick_overrides` from
`econ_sim.new_logic.agent_logic`. During refactor the file was moved/renamed.
Provide a small, well-documented implementation here so the rest of the codebase
and tests can continue to import `econ_sim.logic_modules.agent_logic.merge_tick_overrides`.

The function merges two optional TickDecisionOverrides objects by overlaying
non-None fields. It's intentionally conservative and deterministic.
"""

from typing import Optional, List

from econ_sim.data_access.models import (
    TickDecisionOverrides,
    HouseholdDecisionOverride,
    FirmDecisionOverride,
    BankDecisionOverride,
    GovernmentDecisionOverride,
    CentralBankDecisionOverride,
)

from econ_sim.data_access.models import (
    TickDecisions,
    TickDecisionOverrides,
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
)


def _apply_override(default_decision, override) -> object:
    """Apply an override model onto a default decision model (shallow)."""
    if override is None:
        return default_decision
    updates = {
        k: v
        for k, v in override.model_dump(exclude_unset=True).items()
        if v is not None
    }
    if not updates:
        return default_decision
    try:
        return default_decision.model_copy(update=updates)
    except Exception:
        return default_decision


def collect_tick_decisions(
    baseline: TickDecisions,
    overrides: Optional[TickDecisionOverrides] = None,
) -> TickDecisions:
    """Merge optional overrides onto baseline decisions.

    This mirrors the previous implementation used by the backup orchestrator.
    """
    households: dict[int, HouseholdDecision] = {
        hid: decision.model_copy() for hid, decision in baseline.households.items()
    }
    firm: FirmDecision = baseline.firm.model_copy()
    bank: BankDecision = baseline.bank.model_copy()
    government: GovernmentDecision = baseline.government.model_copy()
    central_bank: CentralBankDecision = baseline.central_bank.model_copy()

    if overrides is not None:
        for household_id, override in overrides.households.items():
            target = households.get(household_id)
            if target is None:
                raise ValueError(
                    f"Override provided for unknown household {household_id}"
                )
            households[household_id] = _apply_override(target, override)

        if overrides.firm is not None:
            firm = _apply_override(firm, overrides.firm)
        if overrides.bank is not None:
            bank = _apply_override(bank, overrides.bank)
        if overrides.government is not None:
            government = _apply_override(government, overrides.government)
        if overrides.central_bank is not None:
            central_bank = _apply_override(central_bank, overrides.central_bank)

    return TickDecisions(
        households=households,
        firm=firm,
        bank=bank,
        government=government,
        central_bank=central_bank,
    )


def _merge_model(target, src):
    """Helper: overlay non-None fields from src into target. Returns new model or None."""
    if src is None:
        return target
    if target is None:
        return src
    data = target.model_dump()
    src_data = src.model_dump()
    # overlay fields where src has non-None
    for k, v in src_data.items():
        if v is not None:
            data[k] = v
    return target.model_validate(data)


def merge_tick_overrides(
    combined: Optional[TickDecisionOverrides],
    overrides: Optional[TickDecisionOverrides],
) -> Optional[TickDecisionOverrides]:
    """Merge two TickDecisionOverrides into one. Later overrides take precedence.

    - households: merge per-household overrides (override entire HouseholdDecisionOverride)
    - firm/bank/government/central_bank: overlay non-None fields
    """
    if overrides is None:
        return combined
    if combined is None:
        return overrides

    # merge households dict: later keys overwrite
    combined_households = dict(combined.households)
    for hid, hov in overrides.households.items():
        combined_households[hid] = hov

    firm = _merge_model(combined.firm, overrides.firm)
    bank = _merge_model(combined.bank, overrides.bank)
    government = _merge_model(combined.government, overrides.government)
    central_bank = _merge_model(combined.central_bank, overrides.central_bank)

    merged = TickDecisionOverrides(
        households=combined_households,
        firm=firm,
        bank=bank,
        government=government,
        central_bank=central_bank,
        bond_bids=(combined.bond_bids or []) + (overrides.bond_bids or []),
        issuance_plan=(
            overrides.issuance_plan
            if getattr(overrides, "issuance_plan", None) is not None
            else combined.issuance_plan
        ),
    )
    return merged

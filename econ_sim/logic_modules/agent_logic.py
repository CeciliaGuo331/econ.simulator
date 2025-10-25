"""Compatibility shim: provide minimal helper used by script registry.

This repository previously exposed `merge_tick_overrides` from
`econ_sim.new_logic.agent_logic`. During refactor the file was moved/renamed.
Provide a small, well-documented implementation here so the rest of the codebase
and tests can continue to import `econ_sim.logic_modules.agent_logic.merge_tick_overrides`.

The function merges two optional TickDecisionOverrides objects by overlaying
non-None fields. It's intentionally conservative and deterministic.
"""

from typing import Optional

from econ_sim.data_access.models import (
    TickDecisionOverrides,
    HouseholdDecisionOverride,
    FirmDecisionOverride,
    BankDecisionOverride,
    GovernmentDecisionOverride,
    CentralBankDecisionOverride,
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
    )
    return merged

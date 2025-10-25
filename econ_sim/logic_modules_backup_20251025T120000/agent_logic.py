"""用于将基线决策与玩家/脚本覆盖合并的工具函数集合。

本模块负责在数据层面合并决策：将 fallback/baseline 决策与来自用户脚本的
overrides 做局部覆盖（partial updates），并保证合并结果满足后续市场逻辑的
数据结构约定。模块仅处理决策合并，不涉及 IO 或持久化。
"""

from __future__ import annotations

from typing import Dict, Optional

from ..data_access.models import (
    BankDecision,
    BankDecisionOverride,
    CentralBankDecision,
    CentralBankDecisionOverride,
    FirmDecision,
    FirmDecisionOverride,
    GovernmentDecision,
    GovernmentDecisionOverride,
    HouseholdDecision,
    HouseholdDecisionOverride,
    TickDecisionOverrides,
    TickDecisions,
)


def _apply_override(default_decision, override) -> object:
    """根据玩家覆盖内容更新默认决策。

    仅对覆盖对象中明确提供的字段进行替换；未提供的字段保持默认值，
    这样可以做到局部覆盖而非整体替换。
    """
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
    baseline: TickDecisions,
    overrides: Optional[TickDecisionOverrides] = None,
) -> TickDecisions:
    """Merge optional overrides onto baseline decisions."""

    households: Dict[int, HouseholdDecision] = {
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


def merge_tick_overrides(
    base: Optional[TickDecisionOverrides],
    overlay: Optional[TickDecisionOverrides],
) -> Optional[TickDecisionOverrides]:
    """合并两个决策覆盖对象，后者优先级更高。"""

    if overlay is None:
        return base
    if base is None:
        return overlay

    def _merge_model(base_model, overlay_model):
        if overlay_model is None:
            return base_model
        if base_model is None:
            return overlay_model
        updates = {
            key: value
            for key, value in overlay_model.model_dump(exclude_unset=True).items()
            if value is not None
        }
        if not updates:
            return base_model
        return base_model.model_copy(update=updates)

    households: Dict[int, HouseholdDecisionOverride] = {
        hid: decision for hid, decision in base.households.items()
    }
    for hid, decision in overlay.households.items():
        current = households.get(hid)
        households[hid] = _merge_model(current, decision)

    merged = TickDecisionOverrides(
        households=households,
        firm=_merge_model(base.firm, overlay.firm),
        bank=_merge_model(base.bank, overlay.bank),
        government=_merge_model(base.government, overlay.government),
        central_bank=_merge_model(base.central_bank, overlay.central_bank),
    )

    if (
        not merged.households
        and merged.firm is None
        and merged.bank is None
        and merged.government is None
        and merged.central_bank is None
    ):
        return None
    return merged

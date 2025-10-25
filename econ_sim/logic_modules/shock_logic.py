"""生成并应用家户层面的异质性外生冲击。

本模块负责为每个 household 在每个 tick 生成可配置的能力乘数（ability_multiplier）
与资产变动（asset_delta）。实现保障了：总体无净冲击（总 asset_delta 近似为 0），
且单户冲击被裁剪到配置允许的最大比例范围内。生成使用基于仿真 id 与 tick 的稳定
种子以保证可重现性。
"""

from __future__ import annotations

from typing import Dict, Any

import numpy as np

from ..data_access.models import HouseholdShock, WorldState


def _stable_seed(simulation_id: str, base_seed: int, tick: int) -> int:
    """根据仿真实例与 Tick 构造稳定的随机种子。"""

    # 使用 32 位掩码避免平台差异，同时让 tick 推进时产生不同的伪随机序列。
    return ((hash(simulation_id) & 0xFFFFFFFF) ^ (base_seed + tick * 9973)) & 0xFFFFFFFF


def generate_household_shocks(
    world_state: WorldState, config: Any
) -> Dict[int, HouseholdShock]:
    """为所有家户生成本 Tick 的能力与资产冲击。"""

    households = world_state.households
    count = len(households)
    if count == 0:
        return {}

    ability_std = max(0.0, world_state.features.household_shock_ability_std)
    asset_std = max(0.0, world_state.features.household_shock_asset_std)
    max_fraction = np.clip(world_state.features.household_shock_max_fraction, 0.0, 0.9)

    ids = sorted(households.keys())
    cash_values = np.array(
        [households[hid].balance_sheet.cash for hid in ids], dtype=float
    )

    rng = np.random.default_rng(
        _stable_seed(
            world_state.simulation_id, config.simulation.seed, world_state.tick + 1
        )
    )

    # 能力冲击：以 1 为基准乘数，扰动项由配置的标准差控制，并在样本上去均值以
    # 保证整体没有系统性偏移。
    ability_raw = rng.normal(loc=0.0, scale=ability_std, size=count)
    ability_raw -= ability_raw.mean()
    ability_multiplier = 1.0 + ability_raw
    lower_bound = 1.0 - max_fraction
    upper_bound = 1.0 + max_fraction
    ability_multiplier = np.clip(ability_multiplier, lower_bound, upper_bound)

    # 资产冲击：基于家庭现金头寸加权扰动，随后进行均值校正，最终裁剪到每户允许的最大比例。
    asset_raw = rng.normal(loc=0.0, scale=asset_std, size=count)
    asset_raw -= asset_raw.mean()
    asset_deltas = cash_values * asset_raw

    if count > 1:
        asset_deltas -= asset_deltas.mean()

    max_bounds = cash_values * max_fraction
    asset_deltas = np.clip(asset_deltas, -max_bounds, max_bounds)

    if count > 1:
        correction = asset_deltas.mean()
        if abs(correction) > 1e-6:
            asset_deltas -= correction
    if count:
        total_residual = asset_deltas.sum()
        if abs(total_residual) > 1e-6:
            asset_deltas[-1] -= total_residual

    shocks: Dict[int, HouseholdShock] = {}
    for idx, hid in enumerate(ids):
        shocks[hid] = HouseholdShock(
            ability_multiplier=float(ability_multiplier[idx]),
            asset_delta=float(asset_deltas[idx]),
        )

    return shocks


def apply_household_shocks_for_decision(
    world_state: WorldState, shocks: Dict[int, HouseholdShock]
) -> WorldState:
    """返回应用资产冲击后的世界状态视图，供决策阶段使用。"""

    if not shocks:
        return world_state

    state_copy = world_state.model_copy(deep=True)
    state_copy.household_shocks = {
        hid: shock.model_copy(deep=True) for hid, shock in shocks.items()
    }

    for hid, shock in shocks.items():
        household = state_copy.households.get(hid)
        if household is None:
            continue
        household.balance_sheet.cash = max(
            0.0, household.balance_sheet.cash + shock.asset_delta
        )

    return state_copy


__all__ = [
    "generate_household_shocks",
    "apply_household_shocks_for_decision",
]

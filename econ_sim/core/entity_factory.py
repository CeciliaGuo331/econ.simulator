"""Factory helpers for constructing default entity states on demand."""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from ..data_access.models import (
    AgentKind,
    BalanceSheet,
    BankState,
    CentralBankState,
    FirmState,
    GovernmentState,
    HouseholdState,
    MacroState,
    SimulationFeatures,
)
from ..utils.settings import WorldConfig


def _rng_for(
    config: WorldConfig, kind: AgentKind, entity_id: Optional[int | str]
) -> np.random.Generator:
    base_seed = int(config.simulation.seed or 0)
    salt = hash((kind.value, entity_id)) & ((1 << 63) - 1)
    seed = (base_seed + salt) % (1 << 63)
    if seed == 0:
        seed = 1
    return np.random.default_rng(seed)


def create_household_state(config: WorldConfig, household_id: int) -> HouseholdState:
    rng = _rng_for(config, AgentKind.HOUSEHOLD, household_id)
    markets = config.markets

    skill = float(max(0.4, rng.normal(1.0, 0.15)))
    preference = float(np.clip(rng.normal(0.5, 0.1), 0.2, 0.8))
    cash = float(rng.uniform(200.0, 400.0))
    deposits = float(rng.uniform(100.0, 200.0))
    inventory_goods = float(np.clip(rng.normal(2.0, 1.0), 0.0, 10.0))

    balance_sheet = BalanceSheet(
        cash=cash,
        deposits=deposits,
        loans=0.0,
        inventory_goods=inventory_goods,
    )

    reservation_wage = float(
        np.clip(markets.labor.base_wage * skill * 0.8, 40.0, 120.0)
    )

    return HouseholdState(
        id=household_id,
        balance_sheet=balance_sheet,
        skill=skill,
        preference=preference,
        reservation_wage=reservation_wage,
    )


def create_firm_state(config: WorldConfig, entity_id: str) -> FirmState:
    rng = _rng_for(config, AgentKind.FIRM, entity_id)
    sim_cfg = config.simulation
    markets = config.markets

    inventory_base = float(
        sim_cfg.num_households * markets.goods.subsistence_consumption * 2
    )

    balance_sheet = BalanceSheet(
        cash=50000.0,
        deposits=10000.0,
        loans=0.0,
        inventory_goods=inventory_base,
    )

    price = markets.goods.base_price
    wage_offer = markets.labor.base_wage
    productivity = float(np.clip(rng.normal(1.0, 0.1), 0.6, 1.4))

    return FirmState(
        id=entity_id,
        balance_sheet=balance_sheet,
        price=price,
        wage_offer=wage_offer,
        productivity=productivity,
        employees=[],
    )


def create_government_state(config: WorldConfig, entity_id: str) -> GovernmentState:
    policies = config.policies
    balance_sheet = BalanceSheet(
        cash=100000.0, deposits=0.0, loans=0.0, inventory_goods=0.0
    )

    return GovernmentState(
        id=entity_id,
        balance_sheet=balance_sheet,
        tax_rate=policies.tax_rate,
        unemployment_benefit=policies.unemployment_benefit,
        spending=policies.government_spending,
    )


def create_central_bank_state(config: WorldConfig, entity_id: str) -> CentralBankState:
    bank_policy = config.policies.central_bank
    return CentralBankState(
        id=entity_id,
        base_rate=bank_policy.base_rate,
        reserve_ratio=bank_policy.reserve_ratio,
        inflation_target=bank_policy.inflation_target,
        unemployment_target=bank_policy.unemployment_target,
    )


def create_bank_state(
    config: WorldConfig,
    entity_id: str,
    households: Optional[Dict[int, HouseholdState]] = None,
) -> BankState:
    deposits = 0.0
    if households:
        deposits = float(sum(h.balance_sheet.deposits for h in households.values()))
    if deposits <= 0.0:
        deposits = float(config.simulation.num_households * 150.0)

    balance_sheet = BalanceSheet(
        cash=200000.0,
        deposits=deposits,
        loans=0.0,
        inventory_goods=0.0,
    )

    finance = config.markets.finance

    return BankState(
        id=entity_id,
        balance_sheet=balance_sheet,
        deposit_rate=finance.deposit_rate,
        loan_rate=finance.loan_rate,
    )


def create_macro_state() -> MacroState:
    return MacroState(
        gdp=0.0,
        inflation=0.0,
        unemployment_rate=1.0,
        price_index=100.0,
        wage_index=100.0,
    )


def create_simulation_features(config: WorldConfig) -> SimulationFeatures:
    return SimulationFeatures()


__all__ = [
    "create_household_state",
    "create_firm_state",
    "create_government_state",
    "create_central_bank_state",
    "create_bank_state",
    "create_macro_state",
    "create_simulation_features",
]

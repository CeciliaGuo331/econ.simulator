"""Baseline heuristics for each agent type used for smoke testing."""

from __future__ import annotations

from typing import Dict

import numpy as np

from ..data_access.models import (
    BankDecision,
    BankState,
    CentralBankDecision,
    CentralBankState,
    FirmDecision,
    FirmState,
    GovernmentDecision,
    GovernmentState,
    HouseholdDecision,
    HouseholdState,
    PublicMarketData,
    WorldState,
)
from ..utils.settings import WorldConfig


class BaseHouseholdStrategy:
    """Simple consumption-savings rule for households."""

    def __init__(self, config: WorldConfig) -> None:
        self.config = config

    def decide(
        self, household: HouseholdState, market: PublicMarketData
    ) -> HouseholdDecision:
        available_income = household.balance_sheet.cash + household.wage_income
        subsistence_cost = (
            self.config.markets.goods.subsistence_consumption * market.goods_price
        )
        discretionary_budget = max(0.0, available_income - subsistence_cost)
        preference_weight = np.clip(household.preference, 0.1, 0.9)
        planned_consumption = (
            subsistence_cost + discretionary_budget * preference_weight * 0.6
        )
        planned_consumption = float(min(planned_consumption, available_income))

        # Target savings rate increases when already employed and cash is low.
        savings_rate = 0.2 + (0.1 if available_income > subsistence_cost * 1.5 else 0.0)
        savings_rate = float(np.clip(savings_rate, 0.0, 0.8))

        labor_supply = (
            1.0 if household.employment_status.name.startswith("UNEMP") else 0.8
        )

        return HouseholdDecision(
            labor_supply=labor_supply,
            consumption_budget=planned_consumption,
            savings_rate=savings_rate,
        )


class BaseFirmStrategy:
    """Firm adjusts production and wage offers based on recent sales."""

    def __init__(self, config: WorldConfig) -> None:
        self.config = config

    def decide(self, firm: FirmState, world: WorldState) -> FirmDecision:
        target_inventory = (
            self.config.simulation.num_households
            * self.config.markets.goods.subsistence_consumption
        )
        inventory_gap = target_inventory - firm.balance_sheet.inventory_goods
        expected_demand = max(target_inventory, firm.last_sales * 1.1)
        planned_production = max(expected_demand + inventory_gap, 0.0)

        effective_productivity = max(firm.productivity, 0.1)
        desired_workers = int(np.ceil(planned_production / effective_productivity))
        hiring_demand = max(0, desired_workers - len(firm.employees))

        price_adjustment = 1.0
        if firm.balance_sheet.inventory_goods < target_inventory * 0.8:
            price_adjustment = 1.05
        elif firm.balance_sheet.inventory_goods > target_inventory * 1.2:
            price_adjustment = 0.97

        new_price = float(
            np.clip(firm.price * price_adjustment, 0.5 * firm.price, 2.0 * firm.price)
        )
        wage_offer = float(
            np.clip(
                self.config.markets.labor.base_wage * (1 + hiring_demand * 0.01),
                50.0,
                200.0,
            )
        )

        return FirmDecision(
            price=new_price,
            planned_production=float(planned_production),
            wage_offer=wage_offer,
            hiring_demand=hiring_demand,
        )


class BaseGovernmentStrategy:
    """Government keeps tax rate near policy setting and smooths employment."""

    def __init__(self, config: WorldConfig) -> None:
        self.config = config

    def decide(
        self, government: GovernmentState, macro_unemployment: float
    ) -> GovernmentDecision:
        target_tax = self.config.policies.tax_rate
        tax_rate = float(
            np.clip(0.5 * government.tax_rate + 0.5 * target_tax, 0.05, 0.6)
        )

        unemployment_gap = max(0.0, macro_unemployment - 0.07)
        additional_jobs = int(
            round(unemployment_gap * self.config.simulation.num_households * 0.2)
        )
        government_jobs = max(
            self.config.markets.labor.government_jobs,
            len(government.employees) + additional_jobs,
        )

        transfer_budget = float(
            self.config.policies.unemployment_benefit
            * self.config.simulation.num_households
            * unemployment_gap
        )

        return GovernmentDecision(
            tax_rate=tax_rate,
            government_jobs=government_jobs,
            transfer_budget=transfer_budget,
        )


class BaseBankStrategy:
    """Bank adjusts rates with profitability and central bank policy."""

    def __init__(self, config: WorldConfig) -> None:
        self.config = config

    def decide(self, bank: BankState, central_bank: CentralBankState) -> BankDecision:
        policy_rate = central_bank.base_rate
        spread = 0.03
        loan_rate = float(np.clip(policy_rate + spread, 0.02, 0.25))
        deposit_rate = float(np.clip(policy_rate * 0.6, 0.0, loan_rate - 0.005))

        loanable_funds = max(
            0.0,
            bank.balance_sheet.deposits * (1 - central_bank.reserve_ratio)
            - bank.balance_sheet.loans,
        )
        return BankDecision(
            deposit_rate=deposit_rate,
            loan_rate=loan_rate,
            loan_supply=float(loanable_funds),
        )


class BaseCentralBankStrategy:
    """Taylor-rule style adjustment of the policy rate."""

    def __init__(self, config: WorldConfig) -> None:
        self.config = config

    def decide(
        self, central_bank: CentralBankState, macro: PublicMarketData
    ) -> CentralBankDecision:
        inflation_gap = macro.inflation - central_bank.inflation_target
        unemployment_gap = macro.unemployment_rate - central_bank.unemployment_target

        policy_rate = (
            central_bank.base_rate + 0.5 * inflation_gap - 0.3 * unemployment_gap
        )
        policy_rate = float(np.clip(policy_rate, 0.0, 0.25))

        reserve_ratio = float(
            np.clip(central_bank.reserve_ratio + 0.1 * unemployment_gap, 0.05, 0.3)
        )

        return CentralBankDecision(policy_rate=policy_rate, reserve_ratio=reserve_ratio)


class StrategyBundle:
    """Convenience container for baseline strategies."""

    def __init__(self, config: WorldConfig, world_state: WorldState) -> None:
        self.households: Dict[int, BaseHouseholdStrategy] = {
            hid: BaseHouseholdStrategy(config) for hid in world_state.households
        }
        self.firm = BaseFirmStrategy(config)
        self.government = BaseGovernmentStrategy(config)
        self.bank = BaseBankStrategy(config)
        self.central_bank = BaseCentralBankStrategy(config)

    def household_strategy(self, household_id: int) -> BaseHouseholdStrategy:
        return self.households[household_id]

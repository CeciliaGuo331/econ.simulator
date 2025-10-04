"""Core market clearing logic for a simulation tick."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from ..data_access.models import (
    AgentKind,
    BankState,
    CentralBankState,
    EmploymentStatus,
    FirmState,
    GovernmentState,
    HouseholdState,
    MacroState,
    StateUpdateCommand,
    TickDecisions,
    TickLogEntry,
    WorldState,
)
from ..utils.settings import WorldConfig


@dataclass
class WorkingState:
    households: Dict[int, HouseholdState]
    firm: FirmState
    government: GovernmentState
    bank: BankState
    central_bank: CentralBankState
    macro: MacroState


@dataclass
class TickEconomyMetrics:
    goods_sold: float = 0.0
    consumption_value: float = 0.0
    wage_payments_firm: float = 0.0
    wage_payments_government: float = 0.0
    transfers: float = 0.0
    taxes: float = 0.0
    unemployment_rate: float = 1.0
    price_level: float = 0.0
    wage_level: float = 0.0


def execute_tick_logic(
    world_state: WorldState,
    decisions: TickDecisions,
    config: WorldConfig,
) -> Tuple[List[StateUpdateCommand], List[TickLogEntry]]:
    working = _clone_world_state(world_state)
    metrics = TickEconomyMetrics()
    logs: List[TickLogEntry] = []

    _apply_central_bank_policy(working, decisions)

    labor_log = _resolve_labor_market(working, decisions, config, metrics, world_state)
    logs.append(labor_log)

    production_log = _run_production_phase(
        working, decisions, config, metrics, world_state
    )
    logs.append(production_log)

    finance_logs = _process_income_support(
        working, decisions, config, metrics, world_state
    )
    logs.extend(finance_logs)

    goods_log = _clear_goods_market(working, decisions, config, metrics, world_state)
    logs.append(goods_log)

    savings_log = _process_savings(working, decisions, config, metrics, world_state)
    logs.append(savings_log)

    tax_log = _collect_taxes(working, decisions, config, metrics, world_state)
    logs.append(tax_log)

    macro_update = _update_macro_metrics(working, metrics, world_state)

    updates = _build_state_updates(world_state, working, macro_update)

    return updates, logs


def _clone_world_state(world_state: WorldState) -> WorkingState:
    return WorkingState(
        households={
            hid: household.model_copy(deep=True)
            for hid, household in world_state.households.items()
        },
        firm=world_state.firm.model_copy(deep=True),
        government=world_state.government.model_copy(deep=True),
        bank=world_state.bank.model_copy(deep=True),
        central_bank=world_state.central_bank.model_copy(deep=True),
        macro=world_state.macro.model_copy(deep=True),
    )


def _apply_central_bank_policy(working: WorkingState, decisions: TickDecisions) -> None:
    working.central_bank.base_rate = decisions.central_bank.policy_rate
    working.central_bank.reserve_ratio = decisions.central_bank.reserve_ratio


def _resolve_labor_market(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    firm = working.firm
    government = working.government

    unemployed_candidates = [
        working.households[hid]
        for hid, decision in decisions.households.items()
        if working.households[hid].employment_status is EmploymentStatus.UNEMPLOYED
        and decision.labor_supply > 0.5
    ]
    unemployed_candidates.sort(key=lambda h: h.skill, reverse=True)

    desired_firm_workers = max(0, len(firm.employees) + decisions.firm.hiring_demand)
    firm_employees = set(firm.employees)
    for candidate in unemployed_candidates:
        if len(firm_employees) >= desired_firm_workers:
            break
        firm_employees.add(candidate.id)
        candidate.employment_status = EmploymentStatus.EMPLOYED_FIRM
        candidate.employer_id = firm.id
        candidate.wage_income = decisions.firm.wage_offer

    desired_government_jobs = max(
        decisions.government.government_jobs, len(government.employees)
    )
    government_employees = set(government.employees)
    for candidate in unemployed_candidates:
        if candidate.id in firm_employees:
            continue
        if len(government_employees) >= desired_government_jobs:
            break
        government_employees.add(candidate.id)
        candidate.employment_status = EmploymentStatus.EMPLOYED_GOVERNMENT
        candidate.employer_id = government.id
        candidate.wage_income = decisions.firm.wage_offer * 0.8

    firm.employees = sorted(firm_employees)
    government.employees = sorted(government_employees)

    total_employed = len(firm.employees) + len(government.employees)
    metrics.unemployment_rate = float(
        np.clip(1.0 - total_employed / max(1.0, len(working.households)), 0.0, 1.0)
    )

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="labor_market_cleared",
        context={
            "firm_headcount": len(firm.employees),
            "government_headcount": len(government.employees),
            "unemployment_rate": metrics.unemployment_rate,
        },
    )


def _run_production_phase(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    firm = working.firm
    capacity = max(1, len(firm.employees)) * max(firm.productivity, 0.1)
    produced_goods = float(np.clip(decisions.firm.planned_production, 0.0, capacity))
    firm.balance_sheet.inventory_goods = max(
        0.0, firm.balance_sheet.inventory_goods + produced_goods
    )
    firm.price = decisions.firm.price
    firm.wage_offer = decisions.firm.wage_offer
    metrics.price_level = firm.price
    metrics.wage_level = firm.wage_offer

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="production_phase_completed",
        context={
            "produced_goods": produced_goods,
            "inventory": firm.balance_sheet.inventory_goods,
        },
    )


def _process_income_support(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> List[TickLogEntry]:
    firm = working.firm
    government = working.government
    logs: List[TickLogEntry] = []

    firm_payroll = 0.0
    for hid in firm.employees:
        household = working.households[hid]
        firm_payroll += decisions.firm.wage_offer
        household.balance_sheet.cash += decisions.firm.wage_offer

    metrics.wage_payments_firm = firm_payroll
    firm.balance_sheet.cash = max(0.0, firm.balance_sheet.cash - firm_payroll)

    gov_payroll = 0.0
    for hid in government.employees:
        household = working.households[hid]
        wage = decisions.firm.wage_offer * 0.8
        gov_payroll += wage
        household.balance_sheet.cash += wage

    metrics.wage_payments_government = gov_payroll
    government.balance_sheet.cash = max(
        0.0, government.balance_sheet.cash - gov_payroll
    )

    benefit_total = 0.0
    benefit = config.policies.unemployment_benefit
    for household in working.households.values():
        if household.employment_status is EmploymentStatus.UNEMPLOYED:
            household.balance_sheet.cash += benefit
            benefit_total += benefit

    metrics.transfers = benefit_total
    government.balance_sheet.cash = max(
        0.0, government.balance_sheet.cash - benefit_total
    )

    logs.append(
        TickLogEntry(
            tick=world_state.tick,
            day=world_state.day,
            message="wages_disbursed",
            context={
                "firm_payroll": firm_payroll,
                "government_payroll": gov_payroll,
                "benefits": benefit_total,
            },
        )
    )

    return logs


def _clear_goods_market(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    firm = working.firm
    price = max(0.01, firm.price)

    total_goods_demand = 0.0
    planned_goods: Dict[int, float] = {}

    for hid, decision in decisions.households.items():
        household = working.households[hid]
        affordable = household.balance_sheet.cash / price
        planned = min(decision.consumption_budget / price, affordable)
        planned = float(np.clip(planned, 0.0, 200.0))
        planned_goods[hid] = planned
        total_goods_demand += planned

    available_goods = firm.balance_sheet.inventory_goods
    allocation_ratio = (
        1.0
        if total_goods_demand <= available_goods
        else available_goods / max(total_goods_demand, 1e-6)
    )

    goods_sold = 0.0
    consumption_value = 0.0
    for hid, planned in planned_goods.items():
        take_goods = planned * allocation_ratio
        payment = take_goods * price
        household = working.households[hid]
        household.balance_sheet.cash = max(0.0, household.balance_sheet.cash - payment)
        household.last_consumption = take_goods
        goods_sold += take_goods
        consumption_value += payment

    firm.balance_sheet.inventory_goods = max(
        0.0, firm.balance_sheet.inventory_goods - goods_sold
    )
    firm.balance_sheet.cash += consumption_value
    firm.last_sales = goods_sold
    metrics.goods_sold = goods_sold
    metrics.consumption_value = consumption_value

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="goods_market_cleared",
        context={"goods_sold": goods_sold, "consumption_value": consumption_value},
    )


def _process_savings(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    bank = working.bank
    total_new_deposits = 0.0

    for hid, decision in decisions.households.items():
        household = working.households[hid]
        savings = household.balance_sheet.cash * decision.savings_rate
        if savings <= 0:
            continue
        household.balance_sheet.cash -= savings
        household.balance_sheet.deposits += savings
        total_new_deposits += savings

    bank.balance_sheet.deposits += total_new_deposits
    bank.balance_sheet.cash += total_new_deposits
    bank.deposit_rate = decisions.bank.deposit_rate
    bank.loan_rate = decisions.bank.loan_rate

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="savings_processed",
        context={"new_deposits": total_new_deposits},
    )


def _collect_taxes(
    working: WorkingState,
    decisions: TickDecisions,
    config: WorldConfig,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> TickLogEntry:
    tax_rate = decisions.government.tax_rate
    government = working.government
    total_tax = 0.0

    for household in working.households.values():
        taxable_income = max(0.0, household.wage_income)
        tax = taxable_income * tax_rate
        if tax <= 0:
            continue
        deduction = min(tax, household.balance_sheet.cash)
        household.balance_sheet.cash -= deduction
        total_tax += deduction

    metrics.taxes = total_tax
    government.tax_rate = tax_rate
    government.balance_sheet.cash += total_tax

    return TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="taxes_collected",
        context={"tax_collected": total_tax},
    )


def _update_macro_metrics(
    working: WorkingState,
    metrics: TickEconomyMetrics,
    world_state: WorldState,
) -> StateUpdateCommand:
    previous_price = working.macro.price_index or metrics.price_level or 100.0
    price_level = metrics.price_level or previous_price
    price_index = 0.9 * previous_price + 0.1 * price_level

    previous_wage = working.macro.wage_index or metrics.wage_level or 100.0
    wage_level = metrics.wage_level or previous_wage
    wage_index = 0.9 * previous_wage + 0.1 * wage_level

    inflation = 0.0
    if previous_price:
        inflation = (price_index - previous_price) / previous_price

    gdp = (
        metrics.consumption_value
        + metrics.wage_payments_government
        + metrics.wage_payments_firm
        + metrics.transfers
    )

    working.macro.gdp = gdp
    working.macro.inflation = inflation
    working.macro.unemployment_rate = metrics.unemployment_rate
    working.macro.price_index = price_index
    working.macro.wage_index = wage_index

    return StateUpdateCommand.assign(
        AgentKind.MACRO,
        agent_id=None,
        gdp=gdp,
        inflation=inflation,
        unemployment_rate=metrics.unemployment_rate,
        price_index=price_index,
        wage_index=wage_index,
    )


def _build_state_updates(
    original: WorldState,
    working: WorkingState,
    macro_update: StateUpdateCommand,
) -> List[StateUpdateCommand]:
    updates: List[StateUpdateCommand] = [macro_update]

    for hid, household in working.households.items():
        original_household = original.households[hid]
        if household.model_dump() != original_household.model_dump():
            updates.append(
                StateUpdateCommand.assign(
                    AgentKind.HOUSEHOLD,
                    agent_id=hid,
                    balance_sheet=household.balance_sheet.model_dump(),
                    employment_status=household.employment_status.value,
                    employer_id=household.employer_id,
                    wage_income=household.wage_income,
                    last_consumption=household.last_consumption,
                )
            )

    if working.firm.model_dump() != original.firm.model_dump():
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.FIRM,
                agent_id=working.firm.id,
                balance_sheet=working.firm.balance_sheet.model_dump(),
                price=working.firm.price,
                wage_offer=working.firm.wage_offer,
                employees=working.firm.employees,
                last_sales=working.firm.last_sales,
            )
        )

    if working.government.model_dump() != original.government.model_dump():
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT,
                agent_id=working.government.id,
                balance_sheet=working.government.balance_sheet.model_dump(),
                tax_rate=working.government.tax_rate,
                employees=working.government.employees,
            )
        )

    if working.bank.model_dump() != original.bank.model_dump():
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.BANK,
                agent_id=working.bank.id,
                balance_sheet=working.bank.balance_sheet.model_dump(),
                deposit_rate=working.bank.deposit_rate,
                loan_rate=working.bank.loan_rate,
            )
        )

    if working.central_bank.model_dump() != original.central_bank.model_dump():
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.CENTRAL_BANK,
                agent_id=working.central_bank.id,
                base_rate=working.central_bank.base_rate,
                reserve_ratio=working.central_bank.reserve_ratio,
            )
        )

    return updates

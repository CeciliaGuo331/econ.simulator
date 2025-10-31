"""临时 baseline 实现，用于开发早期的 smoke tests。

这个模块会基于世界快照生成符合 `TickDecisions` 的简单决策。
最终的 baseline 会更复杂并替换这里的实现。
"""

from __future__ import annotations

from typing import Dict

from ..data_access.models import (
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
    TickDecisions,
    WorldState,
    AgentKind,
)
from ..utils.settings import get_world_config


def generate_baseline_decisions(world_state: WorldState) -> TickDecisions:
    """Very small baseline: simple heuristics to drive the market loop.

    Args:
        world_state: current WorldState snapshot

    Returns:
        TickDecisions: a decision bundle for the tick
    """
    households: Dict[int, HouseholdDecision] = {}

    for hid, h in world_state.households.items():
        cash = float(h.balance_sheet.cash or 0.0)
        # simple consumption rule: spend 10% of cash but cap to a small number
        consumption_budget = min(cash * 0.1, 10.0)
        savings_rate = 0.1
        labor_supply = 0.5 if h.employment_status.name == "UNEMPLOYED" else 0.0
        # education decision: only allow changing on daily decision tick
        is_studying = False
        education_payment = 0.0
        try:
            market = world_state.get_public_market_data()
            is_daily = bool(getattr(market, "is_daily_decision_tick", False))
        except Exception:
            # if public market data not available, fall back to computing tick_in_day
            try:
                cfg = get_world_config()
                ticks = int(cfg.simulation.ticks_per_day or 1)
            except Exception:
                ticks = 1
            tick_in_day = (int(world_state.tick) % ticks) + 1
            is_daily = tick_in_day == 1

        if is_daily:
            try:
                cfg = get_world_config()
                cost = float(cfg.policies.education_cost_per_day)
                gain = float(cfg.policies.education_gain)
            except Exception:
                cost = 2.0
                gain = 0.05

            assets = float(
                (h.balance_sheet.cash or 0.0) + (h.balance_sheet.deposits or 0.0)
            )
            # approximate expected wage gain from a unit of education investment
            expected_wage_gain = 0.0
            try:
                firm_wage = (
                    float(world_state.firm.wage_offer)
                    if world_state.firm is not None
                    else 0.0
                )
                expected_wage_gain = firm_wage * (0.6 * gain)
            except Exception:
                expected_wage_gain = 0.0

            # Relaxed threshold for testing: lower assets requirement from
            # cost * 20 to cost * 5 so that more households may opt into
            # education in baseline scenarios used for smoke tests.
            if assets > cost * 5 and expected_wage_gain > cost:
                is_studying = True
                education_payment = cost

        households[hid] = HouseholdDecision(
            labor_supply=labor_supply,
            consumption_budget=consumption_budget,
            savings_rate=savings_rate,
            is_studying=is_studying,
            education_payment=education_payment,
        )

    firm = world_state.firm
    if firm is None:
        firm_decision = FirmDecision(
            price=1.0, planned_production=0.0, wage_offer=1.0, hiring_demand=0
        )
    else:
        firm_decision = FirmDecision(
            price=float(firm.price),
            planned_production=max(0.0, min(10.0, 1.0)),
            wage_offer=float(firm.wage_offer),
            hiring_demand=0,
        )

    bank = world_state.bank
    if bank is None:
        bank_decision = BankDecision(deposit_rate=0.01, loan_rate=0.05, loan_supply=0.0)
    else:
        bank_decision = BankDecision(
            deposit_rate=float(bank.deposit_rate),
            loan_rate=float(bank.loan_rate),
            loan_supply=100.0,
        )

    government = world_state.government
    if government is None:
        government_decision = GovernmentDecision(
            tax_rate=0.15, government_jobs=0, transfer_budget=0.0
        )
    else:
        government_decision = GovernmentDecision(
            tax_rate=float(government.tax_rate),
            government_jobs=0,
            transfer_budget=0.0,
        )

    cb = world_state.central_bank
    if cb is None:
        cb_decision = CentralBankDecision(policy_rate=0.02, reserve_ratio=0.08)
    else:
        cb_decision = CentralBankDecision(
            policy_rate=float(cb.base_rate), reserve_ratio=float(cb.reserve_ratio)
        )

    # baseline bond bids: let bank underwrite modest amount at par (unit price=1.0)
    bond_bids = []
    if world_state.bank is not None:
        try:
            bank_id = world_state.bank.id
            # quantity measured in face-value units (baseline: up to bank's cash)
            qty = float(min(world_state.bank.balance_sheet.cash, 1000.0))
            bond_bids = [
                {
                    "buyer_kind": AgentKind.BANK,
                    "buyer_id": bank_id,
                    "price": 1.0,
                    "quantity": qty,
                }
            ]
        except Exception:
            bond_bids = []

    return TickDecisions(
        households=households,
        firm=firm_decision,
        bank=bank_decision,
        government=government_decision,
        central_bank=cb_decision,
        bond_bids=bond_bids,
    )

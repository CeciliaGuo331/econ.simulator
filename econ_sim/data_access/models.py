"""Pydantic models that define the simulation's domain schema."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EmploymentStatus(str, Enum):
    """Employment status for household agents."""

    UNEMPLOYED = "unemployed"
    EMPLOYED_FIRM = "employed_firm"
    EMPLOYED_GOVERNMENT = "employed_government"


class AgentKind(str, Enum):
    HOUSEHOLD = "household"
    FIRM = "firm"
    BANK = "bank"
    CENTRAL_BANK = "central_bank"
    GOVERNMENT = "government"
    WORLD = "world"
    MACRO = "macro"


class BalanceSheet(BaseModel):
    cash: float = 0.0
    deposits: float = 0.0
    loans: float = 0.0
    inventory_goods: float = 0.0


class HouseholdState(BaseModel):
    id: int
    balance_sheet: BalanceSheet
    skill: float = 1.0
    preference: float = 0.5
    employment_status: EmploymentStatus = EmploymentStatus.UNEMPLOYED
    employer_id: Optional[str] = None
    wage_income: float = 0.0
    labor_supply: float = 1.0
    last_consumption: float = 0.0
    reservation_wage: float = 60.0


class FirmState(BaseModel):
    id: str = "firm_1"
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    price: float = 10.0
    wage_offer: float = 80.0
    planned_production: float = 0.0
    productivity: float = 1.0
    employees: List[int] = Field(default_factory=list)
    last_sales: float = 0.0


class GovernmentState(BaseModel):
    id: str = "government"
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    tax_rate: float = 0.15
    unemployment_benefit: float = 50.0
    spending: float = 10000.0
    employees: List[int] = Field(default_factory=list)


class BankState(BaseModel):
    id: str = "bank"
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    deposit_rate: float = 0.01
    loan_rate: float = 0.05
    approved_loans: Dict[int, float] = Field(default_factory=dict)


class CentralBankState(BaseModel):
    id: str = "central_bank"
    base_rate: float = 0.03
    reserve_ratio: float = 0.1
    inflation_target: float = 0.02
    unemployment_target: float = 0.05


class MacroState(BaseModel):
    gdp: float = 0.0
    inflation: float = 0.0
    unemployment_rate: float = 0.0
    price_index: float = 100.0
    wage_index: float = 100.0


class PublicMarketData(BaseModel):
    goods_price: float
    wage_offer: float
    deposit_rate: float
    loan_rate: float
    tax_rate: float
    unemployment_rate: float
    inflation: float


class WorldState(BaseModel):
    """Top-level world state snapshot for a simulation tick."""

    simulation_id: str
    tick: int
    day: int
    households: Dict[int, HouseholdState]
    firm: FirmState
    bank: BankState
    government: GovernmentState
    central_bank: CentralBankState
    macro: MacroState

    def get_public_market_data(self) -> PublicMarketData:
        return PublicMarketData(
            goods_price=self.firm.price,
            wage_offer=self.firm.wage_offer,
            deposit_rate=self.bank.deposit_rate,
            loan_rate=self.bank.loan_rate,
            tax_rate=self.government.tax_rate,
            unemployment_rate=self.macro.unemployment_rate,
            inflation=self.macro.inflation,
        )


class StateUpdateCommand(BaseModel):
    """Instruction describing partial state updates for an agent or the world."""

    scope: AgentKind
    agent_id: Optional[int | str] = None
    changes: Dict[str, Any]
    mode: str = Field(default="delta")  # either "delta" or "set"

    @staticmethod
    def delta(
        scope: AgentKind, *, agent_id: Optional[int | str], **changes: float
    ) -> "StateUpdateCommand":
        return StateUpdateCommand(
            scope=scope, agent_id=agent_id, changes=changes, mode="delta"
        )

    @staticmethod
    def assign(
        scope: AgentKind, *, agent_id: Optional[int | str], **changes: float
    ) -> "StateUpdateCommand":
        return StateUpdateCommand(
            scope=scope, agent_id=agent_id, changes=changes, mode="set"
        )


class HouseholdDecision(BaseModel):
    labor_supply: float
    consumption_budget: float
    savings_rate: float


class FirmDecision(BaseModel):
    price: float
    planned_production: float
    wage_offer: float
    hiring_demand: int


class GovernmentDecision(BaseModel):
    tax_rate: float
    government_jobs: int
    transfer_budget: float


class BankDecision(BaseModel):
    deposit_rate: float
    loan_rate: float
    loan_supply: float


class CentralBankDecision(BaseModel):
    policy_rate: float
    reserve_ratio: float


class TickDecisions(BaseModel):
    households: Dict[int, HouseholdDecision]
    firm: FirmDecision
    bank: BankDecision
    government: GovernmentDecision
    central_bank: CentralBankDecision


class HouseholdDecisionOverride(BaseModel):
    labor_supply: Optional[float] = None
    consumption_budget: Optional[float] = None
    savings_rate: Optional[float] = None


class FirmDecisionOverride(BaseModel):
    price: Optional[float] = None
    planned_production: Optional[float] = None
    wage_offer: Optional[float] = None
    hiring_demand: Optional[int] = None


class GovernmentDecisionOverride(BaseModel):
    tax_rate: Optional[float] = None
    government_jobs: Optional[int] = None
    transfer_budget: Optional[float] = None


class BankDecisionOverride(BaseModel):
    deposit_rate: Optional[float] = None
    loan_rate: Optional[float] = None
    loan_supply: Optional[float] = None


class CentralBankDecisionOverride(BaseModel):
    policy_rate: Optional[float] = None
    reserve_ratio: Optional[float] = None


class TickDecisionOverrides(BaseModel):
    households: Dict[int, HouseholdDecisionOverride] = Field(default_factory=dict)
    firm: Optional[FirmDecisionOverride] = None
    bank: Optional[BankDecisionOverride] = None
    government: Optional[GovernmentDecisionOverride] = None
    central_bank: Optional[CentralBankDecisionOverride] = None


class TickLogEntry(BaseModel):
    tick: int
    day: int
    message: str
    context: Dict[str, float | int] = Field(default_factory=dict)


class TickResult(BaseModel):
    world_state: WorldState
    logs: List[TickLogEntry]
    updates: List[StateUpdateCommand]

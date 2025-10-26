"""定义经济仿真领域模型的 Pydantic 数据结构。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from ..utils.settings import get_world_config


class EmploymentStatus(str, Enum):
    """家户代理人在劳动力市场中的就业状态枚举。"""

    UNEMPLOYED = "unemployed"
    EMPLOYED_FIRM = "employed_firm"
    EMPLOYED_GOVERNMENT = "employed_government"


class AgentKind(str, Enum):
    """系统中可出现的主体类型枚举，用于路由状态更新。"""

    HOUSEHOLD = "household"
    FIRM = "firm"
    BANK = "bank"
    CENTRAL_BANK = "central_bank"
    GOVERNMENT = "government"
    WORLD = "world"
    MACRO = "macro"


class BalanceSheet(BaseModel):
    """通用资产负债表，用于记录现金、存款、负债与商品库存。"""

    cash: float = 0.0
    # 商业银行在央行或同业的准备金余额（用于准备金约束与跨行结算）
    reserves: float = 0.0
    deposits: float = 0.0
    loans: float = 0.0
    inventory_goods: float = 0.0


class HouseholdState(BaseModel):
    """家户代理人的完整状态，包括财务、技能与劳动属性。"""

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
    # bond holdings: bond_id -> quantity
    bond_holdings: Dict[str, float] = Field(default_factory=dict)
    # education state
    education_level: float = 0.5
    is_studying: bool = False

    @property
    def productivity(self) -> float:
        """Alias for `skill` to align code (skill) with docs (productivity).

        Note: this is a convenience property and is not included in serialized
        dumps by default. Callers that need productivity for sorting/matching
        should use this property.
        """
        try:
            return float(self.skill)
        except Exception:
            return 1.0


class HouseholdShock(BaseModel):
    """描述家户在单个 Tick 内收到的外生冲击效果。"""

    ability_multiplier: float = 1.0
    asset_delta: float = 0.0


class SimulationFeatures(BaseModel):
    """记录可按仿真实例启用的可选功能开关与参数。"""

    household_shock_enabled: bool = False
    household_shock_ability_std: float = Field(default=0.08, ge=0.0)
    household_shock_asset_std: float = Field(default=0.05, ge=0.0)
    household_shock_max_fraction: float = Field(default=0.4, ge=0.0, le=0.9)


class FirmState(BaseModel):
    """企业代理人的运营状态，覆盖库存、定价、雇员等信息。"""

    id: str = "firm_1"
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    price: float = 10.0
    wage_offer: float = 80.0
    planned_production: float = 0.0
    productivity: float = 1.0
    employees: List[int] = Field(default_factory=list)
    last_sales: float = 0.0


class GovernmentState(BaseModel):
    """政府部门的财政与雇佣状态，含税率、支出与员工名单。"""

    id: str = "government"
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    tax_rate: float = 0.15
    unemployment_benefit: float = 50.0
    spending: float = 10000.0
    employees: List[int] = Field(default_factory=list)
    # debt outstanding per bond id
    debt_outstanding: Dict[str, float] = Field(default_factory=dict)
    # debt instruments registry (bond_id -> BondInstrument)
    debt_instruments: Dict[str, "BondInstrument"] = Field(default_factory=dict)


class BankState(BaseModel):
    """商业银行的资产结构及利率设定。"""

    id: str = "bank"
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    deposit_rate: float = 0.01
    loan_rate: float = 0.05
    approved_loans: Dict[int, float] = Field(default_factory=dict)
    # bond holdings: bond_id -> quantity
    bond_holdings: Dict[str, float] = Field(default_factory=dict)

    @property
    def equity(self) -> float:
        """计算银行净资产（会计恒等式：equity = reserves + loans - deposits）。

        注意：该属性为只读计算字段，用于策略与监控。持久化层不依赖于此字段。
        """
        try:
            bs = self.balance_sheet
            return float(
                (bs.reserves or 0.0) + (bs.loans or 0.0) - (bs.deposits or 0.0)
            )
        except Exception:
            return 0.0


class CentralBankState(BaseModel):
    """央行的政策参数，包括基准利率、准备金率与目标指标。"""

    id: str = "central_bank"
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    base_rate: float = 0.03
    reserve_ratio: float = 0.1
    inflation_target: float = 0.02
    unemployment_target: float = 0.05
    # bond holdings for OMO
    bond_holdings: Dict[str, float] = Field(default_factory=dict)


class MacroState(BaseModel):
    """系统统计生成的宏观指标快照，例如 GDP 与通胀率。"""

    gdp: float = 0.0
    inflation: float = 0.0
    unemployment_rate: float = 0.0
    price_index: float = 100.0
    wage_index: float = 100.0
    # observable market bond yield (set after bond auctions)
    bond_yield: Optional[float] = None


class PublicMarketData(BaseModel):
    """面向所有主体公开的市场信息，用于策略决策。"""

    goods_price: float
    wage_offer: float
    deposit_rate: float
    loan_rate: float
    tax_rate: float
    unemployment_rate: float
    inflation: float
    bond_yield: Optional[float] = None
    # per-tick information
    tick_in_day: Optional[int] = None
    is_daily_decision_tick: Optional[bool] = None


class WorldState(BaseModel):
    """某一 Tick 的世界状态快照，聚合所有主体信息。"""

    simulation_id: str
    tick: int
    day: int
    households: Dict[int, HouseholdState] = Field(default_factory=dict)
    firm: Optional[FirmState] = None
    bank: Optional[BankState] = None
    government: Optional[GovernmentState] = None
    central_bank: Optional[CentralBankState] = None
    macro: MacroState
    household_shocks: Dict[int, HouseholdShock] = Field(default_factory=dict)
    features: SimulationFeatures = Field(default_factory=SimulationFeatures)

    def get_public_market_data(self) -> PublicMarketData:
        """提取公开市场数据，供策略层观察外部环境。"""
        if (
            self.firm is None
            or self.bank is None
            or self.government is None
            or self.central_bank is None
        ):
            raise ValueError("缺少核心主体，无法构造市场数据")
        # compute tick_in_day & is_daily_decision_tick using config.ticks_per_day
        try:
            cfg = get_world_config()
            ticks_per_day = int(cfg.simulation.ticks_per_day or 1)
        except Exception:
            ticks_per_day = 1

        tick_in_day = (int(self.tick) % ticks_per_day) + 1
        is_daily = tick_in_day == 1

        return PublicMarketData(
            goods_price=self.firm.price,
            wage_offer=self.firm.wage_offer,
            deposit_rate=self.bank.deposit_rate,
            loan_rate=self.bank.loan_rate,
            tax_rate=self.government.tax_rate,
            unemployment_rate=self.macro.unemployment_rate,
            inflation=self.macro.inflation,
            bond_yield=self.macro.bond_yield,
            tick_in_day=tick_in_day,
            is_daily_decision_tick=is_daily,
        )

    # Convenience alias: expose household productivity as an alias of skill to match docs
    # (keeps code using `skill` and document references to `productivity` compatible)


class StateUpdateCommand(BaseModel):
    """描述局部状态变更的指令，用于驱动存储层更新。"""

    scope: AgentKind
    agent_id: Optional[int | str] = None
    changes: Dict[str, Any]
    mode: str = Field(default="delta")  # either "delta" or "set"

    @staticmethod
    def delta(
        scope: AgentKind, *, agent_id: Optional[int | str], **changes: float
    ) -> "StateUpdateCommand":
        """创建增量更新指令，将数值与原值相加。"""
        return StateUpdateCommand(
            scope=scope, agent_id=agent_id, changes=changes, mode="delta"
        )

    @staticmethod
    def assign(
        scope: AgentKind, *, agent_id: Optional[int | str], **changes: float
    ) -> "StateUpdateCommand":
        """创建覆盖更新指令，直接写入新的字段值。"""
        return StateUpdateCommand(
            scope=scope, agent_id=agent_id, changes=changes, mode="set"
        )


class HouseholdDecision(BaseModel):
    """家户在当前 Tick 的劳动、消费与储蓄计划。"""

    labor_supply: float
    consumption_budget: float
    savings_rate: float
    # education decision: whether to study (only meaningful on daily decision tick)
    is_studying: bool = False
    # payment towards education for this tick
    education_payment: float = 0.0
    # optional financial orders
    deposit_order: float = 0.0
    withdrawal_order: float = 0.0


class FirmDecision(BaseModel):
    """企业针对生产、定价与招聘的决策。"""

    price: float
    planned_production: float
    wage_offer: float
    hiring_demand: int


class GovernmentDecision(BaseModel):
    """政府在税收、岗位与转移支付方面的决策。"""

    tax_rate: float
    government_jobs: int
    transfer_budget: float
    # optional issuance plan proposed by a government script for this tick
    # issuance_plan: {"volume": float, "min_price": Optional[float]}
    issuance_plan: Optional[Dict[str, Any]] = None


class BankDecision(BaseModel):
    """商业银行设定利率与信贷供给的决策。"""

    deposit_rate: float
    loan_rate: float
    loan_supply: float


class CentralBankDecision(BaseModel):
    """央行调整政策利率与准备金率的决策。"""

    policy_rate: float
    reserve_ratio: float
    # optional OMO operations: list of {"bond_id": str, "side": "buy"|"sell", "quantity": float, "price": float}
    omo_ops: List[Dict[str, Any]] = Field(default_factory=list)


class TickDecisions(BaseModel):
    """一个 Tick 内所有主体的完整决策集合。"""

    households: Dict[int, HouseholdDecision]
    firm: FirmDecision
    bank: BankDecision
    government: GovernmentDecision
    central_bank: CentralBankDecision
    # optional bond bids submitted by participants for this tick's issuance
    # each bid: {"buyer_kind": str, "buyer_id": str|int, "price": float, "quantity": float}
    bond_bids: List[Dict[str, Any]] = Field(default_factory=list)


class HouseholdDecisionOverride(BaseModel):
    """用于覆盖家户默认决策的可选字段。"""

    labor_supply: Optional[float] = None
    consumption_budget: Optional[float] = None
    savings_rate: Optional[float] = None
    is_studying: Optional[bool] = None
    education_payment: Optional[float] = None


class FirmDecisionOverride(BaseModel):
    """用于覆盖企业默认决策的可选字段。"""

    price: Optional[float] = None
    planned_production: Optional[float] = None
    wage_offer: Optional[float] = None
    hiring_demand: Optional[int] = None


class GovernmentDecisionOverride(BaseModel):
    """用于覆盖政府默认决策的可选字段。"""

    tax_rate: Optional[float] = None
    government_jobs: Optional[int] = None
    transfer_budget: Optional[float] = None
    # allow scripts to propose an issuance_plan (volume, min_price) for this tick
    issuance_plan: Optional[Dict[str, Any]] = None


class BankDecisionOverride(BaseModel):
    """用于覆盖银行默认决策的可选字段。"""

    deposit_rate: Optional[float] = None
    loan_rate: Optional[float] = None
    loan_supply: Optional[float] = None


class CentralBankDecisionOverride(BaseModel):
    """用于覆盖央行默认决策的可选字段。"""

    policy_rate: Optional[float] = None
    reserve_ratio: Optional[float] = None


class TickDecisionOverrides(BaseModel):
    """统一封装各主体的决策覆盖输入。"""

    households: Dict[int, HouseholdDecisionOverride] = Field(default_factory=dict)
    firm: Optional[FirmDecisionOverride] = None
    bank: Optional[BankDecisionOverride] = None
    government: Optional[GovernmentDecisionOverride] = None
    central_bank: Optional[CentralBankDecisionOverride] = None
    # optional bond bids that a script may submit for this tick's issuance
    # each bid: {"buyer_kind": str, "buyer_id": str|int, "price": float, "quantity": float}
    bond_bids: List[Dict[str, Any]] = Field(default_factory=list)
    # optional issuance plan proposed by government script
    issuance_plan: Optional[Dict[str, Any]] = None


class TickLogEntry(BaseModel):
    """Tick 执行过程中的日志记录，包含关键信息与上下文。"""

    tick: int
    day: int
    message: str
    context: Dict[str, float | int | str] = Field(default_factory=dict)


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderBookLevel(BaseModel):
    """聚合订单薄档位（按价格聚合）。"""

    price: float
    quantity: float
    side: OrderSide


class MarketRuntime(BaseModel):
    """交易市场运行时快照（撮合视图）。"""

    last_price: float | None = None
    last_quantity: float | None = None
    volume_day: float = 0.0
    bids: List[OrderBookLevel] = Field(default_factory=list)
    asks: List[OrderBookLevel] = Field(default_factory=list)


class TradeRecord(BaseModel):
    """成交记录（撮合结果事件）。"""

    tick: int
    day: int
    buyer_kind: AgentKind
    buyer_id: str
    seller_kind: AgentKind
    seller_id: str
    quantity: float
    price: float
    amount: float


class BondInstrument(BaseModel):
    """简单的债券/国债对象模型（最小化实现）。"""

    id: str
    issuer: str
    face_value: float
    coupon_rate: float
    # coupon_frequency_ticks: number of ticks between coupon payments; 0 means pay only at maturity
    coupon_frequency_ticks: int = 0
    # next tick at which a coupon payment is due; if None, no periodic coupons scheduled
    next_coupon_tick: Optional[int] = None
    maturity_tick: int
    outstanding: float
    holders: Dict[str, float] = Field(default_factory=dict)
    # detailed purchase records to track holding start tick and enable
    # minimum-hold-period rules (list of {buyer_kind, buyer_id, quantity, price, tick})
    purchase_records: List[Dict[str, Any]] = Field(default_factory=list)


class LedgerEntry(BaseModel):
    """账户流水记录。用于主体资产的记账追踪。"""

    tick: int
    day: int
    account_kind: AgentKind
    entity_id: str
    entry_type: str  # e.g. "trade_settlement", "wage_payment", "tax", "loan"
    amount: float
    balance_after: Optional[float] = None
    reference: Optional[str] = None  # optional external ref (trade_id, order_id)


class AgentSnapshotRecord(BaseModel):
    """主体状态快照（草案），用于持久化单主体的局部状态。"""

    tick: int
    day: int
    agent_kind: AgentKind
    entity_id: str
    payload: Dict[str, Any]


class ScriptFailureRecord(BaseModel):
    """单次脚本执行失败的持久化记录。"""

    failure_id: str
    simulation_id: str
    script_id: str
    user_id: str
    agent_kind: AgentKind
    entity_id: str
    message: str
    traceback: str
    occurred_at: datetime


class TickResult(BaseModel):
    """Tick 执行结果，包含更新后的世界状态、日志与指令。"""

    world_state: "WorldState"  # type: ignore[name-defined]
    logs: List[TickLogEntry]
    updates: List[StateUpdateCommand]
    # explicit market signals observed/produced during the tick (e.g. bond_yield)
    market_signals: Dict[str, Any] = Field(default_factory=dict)
    # full list of ledger entries produced during this tick
    ledgers: List["LedgerEntry"] = Field(default_factory=list)

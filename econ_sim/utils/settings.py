"""提供经济仿真所需的配置模型与读取工具。

注意：自本版本起，所有表示利率的字段（如存贷利率、央行政策利率、国债票面利率等）
均采用「每 tick 利率（per-tick rate）」的语义，也就是说这些值直接表示单个 tick 内的利率。
引擎内部不会再对利率做年化到 tick 的自动转换。若需要从年化利率输入，请使用外部工具
或在配置加载前把年化利率转换为等效的 per-tick 利率（示例：若仍希望表达年化 r，
可用 (1 + r) ** (1 / (ticks_per_year)) - 1 转换）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class SimulationParameters(BaseModel):
    """定义仿真运行的时间尺度与规模参数。"""

    name: str = Field(default="default_world")
    ticks_per_day: int = Field(default=3, ge=1)
    max_days: int = Field(default=30, ge=1)
    num_households: int = Field(default=100, ge=1)
    initial_tick: int = Field(default=0, ge=0)
    initial_day: int = Field(default=0, ge=0)
    seed: int = Field(default=42)
    # 最大并发脚本执行数（用于控制沙箱并发）
    script_execution_concurrency: int = Field(default=8, ge=1)


class GoodsMarketConfig(BaseModel):
    """商品市场的关键配置，例如基准价格与温饱消费。"""

    base_price: float = Field(default=10.0, ge=0.0)
    subsistence_consumption: float = Field(default=1.0, ge=0.0)


class LaborMarketConfig(BaseModel):
    """劳动力市场配置，涵盖基准工资与公共岗位数量。"""

    base_wage: float = Field(default=80.0, ge=0.0)
    government_jobs: int = Field(default=20, ge=0)


class FinanceMarketConfig(BaseModel):
    """金融市场配置，如存贷利率等参数。"""

    deposit_rate: float = Field(
        default=0.01,
        description="存款利率（per-tick），即每个 tick 的利率。例如 0.01 表示每个 tick 增长 1%",
    )
    loan_rate: float = Field(
        default=0.05,
        description="贷款利率（per-tick），即每个 tick 的利率。例如 0.05 表示每个 tick 增长 5%",
    )
    # auction_mode: 'random' (doc-specified random allocation) or 'price' (price-priority)
    auction_mode: str = Field(
        default="random", description="Bond auction matching mode: 'random' or 'price'"
    )


class MarketConfig(BaseModel):
    """聚合各类市场配置，便于整体引用。"""

    goods: GoodsMarketConfig = Field(default_factory=GoodsMarketConfig)
    labor: LaborMarketConfig = Field(default_factory=LaborMarketConfig)
    finance: FinanceMarketConfig = Field(default_factory=FinanceMarketConfig)


class CentralBankPolicy(BaseModel):
    """央行政策目标与操作参数的配置集合。"""

    inflation_target: float = Field(default=0.02)
    unemployment_target: float = Field(default=0.05)
    base_rate: float = Field(
        default=0.03,
        description="央行政策利率（per-tick）。本字段表示每个 tick 的政策利率（非年化）",
    )
    reserve_ratio: float = Field(default=0.1)


class PolicyConfig(BaseModel):
    """政府与央行政策配置信息。"""

    tax_rate: float = Field(default=0.15)
    unemployment_benefit: float = Field(default=50.0)
    government_spending: float = Field(default=10000.0)
    # 转移支付默认参数
    means_test_amount: float = Field(default=20.0)
    transfer_threshold: float = Field(default=50.0)
    transfer_funding_policy: str = Field(default="allow_debt")
    allow_partial_payment: bool = Field(default=False)
    central_bank: CentralBankPolicy = Field(default_factory=CentralBankPolicy)
    # bond defaults for marketized issuance
    # 表示国债票面利率（per-tick），用于默认发行时的 coupon 计算
    default_bond_coupon_rate: float = Field(
        default=0.03,
        description="国债票面利率（per-tick），即每个 tick 支付的利率。若需从年化利率转换，请在配置加载前处理",
    )
    default_bond_maturity: int = Field(default=10)
    # education policy parameters (world settings)
    education_cost_per_day: float = Field(
        default=2.0, description="Daily cost for household education"
    )
    education_gain: float = Field(
        default=0.05,
        description="Education increases productivity by this amount per completed daily investment",
    )


class WorldConfig(BaseModel):
    """完整的世界配置对象，包含仿真、市场与政策参数。"""

    simulation: SimulationParameters = Field(default_factory=SimulationParameters)
    markets: MarketConfig = Field(default_factory=MarketConfig)
    policies: PolicyConfig = Field(default_factory=PolicyConfig)
    # 当为 True 时，数据访问层将在持久化前检测并自动修正商业银行记账口径中
    # 的存款总额（将 bank.balance_sheet.deposits 校准为所有非银机构的存款之和），
    # 以保障会计恒等式在外部写入或模块间不一致时仍能统一。可按需在配置中关闭。
    reconcile_deposits: bool = Field(
        default=True,
        description=(
            "Whether to auto-correct bank.deposits to equal the sum of non-bank deposits "
            "when a mismatch is detected prior to persistence."
        ),
    )


def _load_yaml_config(path: Path) -> dict:
    """读取并解析给定路径的 YAML 配置文件。"""
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_world_config(config_path: Optional[Path] = None) -> WorldConfig:
    """从 YAML 文件加载世界配置。

    Parameters
    ----------
    config_path:
        可选的 YAML 配置文件路径。若未指定，则默认读取仓库根目录下
        ``config/world_settings.yaml``。
    """

    if config_path is None:
        config_path = (
            Path(__file__).resolve().parents[2] / "config" / "world_settings.yaml"
        )

    raw = _load_yaml_config(config_path)
    return WorldConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_world_config(config_path: Optional[Path] = None) -> WorldConfig:
    """返回解析后的 :class:`WorldConfig`，并使用 LRU 缓存避免重复读取。"""

    return load_world_config(config_path=config_path)

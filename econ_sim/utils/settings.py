"""提供经济仿真所需的配置模型与读取工具。"""

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

    deposit_rate: float = Field(default=0.01)
    loan_rate: float = Field(default=0.05)


class MarketConfig(BaseModel):
    """聚合各类市场配置，便于整体引用。"""

    goods: GoodsMarketConfig = Field(default_factory=GoodsMarketConfig)
    labor: LaborMarketConfig = Field(default_factory=LaborMarketConfig)
    finance: FinanceMarketConfig = Field(default_factory=FinanceMarketConfig)


class CentralBankPolicy(BaseModel):
    """央行政策目标与操作参数的配置集合。"""

    inflation_target: float = Field(default=0.02)
    unemployment_target: float = Field(default=0.05)
    base_rate: float = Field(default=0.03)
    reserve_ratio: float = Field(default=0.1)


class PolicyConfig(BaseModel):
    """政府与央行政策配置信息。"""

    tax_rate: float = Field(default=0.15)
    unemployment_benefit: float = Field(default=50.0)
    government_spending: float = Field(default=10000.0)
    central_bank: CentralBankPolicy = Field(default_factory=CentralBankPolicy)


class WorldConfig(BaseModel):
    """完整的世界配置对象，包含仿真、市场与政策参数。"""

    simulation: SimulationParameters = Field(default_factory=SimulationParameters)
    markets: MarketConfig = Field(default_factory=MarketConfig)
    policies: PolicyConfig = Field(default_factory=PolicyConfig)


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

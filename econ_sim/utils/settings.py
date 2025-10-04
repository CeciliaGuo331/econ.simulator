"""Configuration utilities for the econ simulator."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class SimulationParameters(BaseModel):
    """Top level timing and scale parameters for a simulation."""

    name: str = Field(default="default_world")
    ticks_per_day: int = Field(default=3, ge=1)
    max_days: int = Field(default=30, ge=1)
    num_households: int = Field(default=100, ge=1)
    initial_tick: int = Field(default=0, ge=0)
    initial_day: int = Field(default=0, ge=0)
    seed: int = Field(default=42)


class GoodsMarketConfig(BaseModel):
    base_price: float = Field(default=10.0, ge=0.0)
    subsistence_consumption: float = Field(default=1.0, ge=0.0)


class LaborMarketConfig(BaseModel):
    base_wage: float = Field(default=80.0, ge=0.0)
    government_jobs: int = Field(default=20, ge=0)


class FinanceMarketConfig(BaseModel):
    deposit_rate: float = Field(default=0.01)
    loan_rate: float = Field(default=0.05)


class MarketConfig(BaseModel):
    goods: GoodsMarketConfig = Field(default_factory=GoodsMarketConfig)
    labor: LaborMarketConfig = Field(default_factory=LaborMarketConfig)
    finance: FinanceMarketConfig = Field(default_factory=FinanceMarketConfig)


class CentralBankPolicy(BaseModel):
    inflation_target: float = Field(default=0.02)
    unemployment_target: float = Field(default=0.05)
    base_rate: float = Field(default=0.03)
    reserve_ratio: float = Field(default=0.1)


class PolicyConfig(BaseModel):
    tax_rate: float = Field(default=0.15)
    unemployment_benefit: float = Field(default=50.0)
    government_spending: float = Field(default=10000.0)
    central_bank: CentralBankPolicy = Field(default_factory=CentralBankPolicy)


class WorldConfig(BaseModel):
    simulation: SimulationParameters = Field(default_factory=SimulationParameters)
    markets: MarketConfig = Field(default_factory=MarketConfig)
    policies: PolicyConfig = Field(default_factory=PolicyConfig)


def _load_yaml_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_world_config(config_path: Optional[Path] = None) -> WorldConfig:
    """Load the world configuration from a YAML file.

    Parameters
    ----------
    config_path:
        Optional path to a YAML configuration file. If omitted, defaults to
        ``config/world_settings.yaml`` relative to the repository root.
    """

    if config_path is None:
        config_path = (
            Path(__file__).resolve().parents[2] / "config" / "world_settings.yaml"
        )

    raw = _load_yaml_config(config_path)
    return WorldConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_world_config(config_path: Optional[Path] = None) -> WorldConfig:
    """Cached helper returning the parsed :class:`WorldConfig`."""

    return load_world_config(config_path=config_path)

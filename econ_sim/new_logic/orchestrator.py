"""最小的 orchestrator wrapper，用于在开发期间调用新的 baseline 与现有的 market logic（暂时）。

目的：提供一个单函数入口 `run_tick_new(world_state)`，产生 decisions 并调用市场逻辑进行清算。
最终会把此处替换为完整的新逻辑实现。
"""

from __future__ import annotations

from typing import List, Tuple

from ..logic_modules.market_logic import execute_tick_logic
from ..data_access.models import StateUpdateCommand, TickLogEntry, WorldState
from .baseline_stub import generate_baseline_decisions
from ..utils.settings import get_world_config


def run_tick_new(
    world_state: WorldState,
) -> Tuple[List[StateUpdateCommand], List[TickLogEntry]]:
    """Run a minimal tick using the temporary baseline and existing market logic.

    This wrapper keeps the execution isolated so tests can exercise the new baseline
    and event entrypoint without touching the full orchestrator plumbing.
    """
    config = get_world_config()
    decisions = generate_baseline_decisions(world_state)
    updates, logs = execute_tick_logic(world_state, decisions, config, shocks=None)
    return updates, logs

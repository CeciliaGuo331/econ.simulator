"""接口契约（草案）——根据 docs/econ_design 编写的轻量 Protocol 定义。

此文件为非侵入性、低风险的接口描述，旨在逐步将现有实现适配为依赖抽象而非具体实现。
它不会改变运行时行为，仅提供类型/契约文档与方便后续重构的入口点。
"""

from __future__ import annotations

from typing import Protocol, Any, Dict, List, Tuple

from ..data_access.models import WorldState, TickDecisions, StateUpdateCommand


class AgentInterface(Protocol):
    """Agent 接口：observe/decide 两步走，决策必须是可序列化的数据对象（Pydantic）。"""

    def observe(self, world_state: WorldState) -> Any: ...

    def decide(self, observation: Any) -> Any: ...


class MarketInterface(Protocol):
    """Market 层接口：负责收集 orders 并执行撮合，返回持久化指令与日志。"""

    def collect_orders(
        self, decisions: TickDecisions, world_state: WorldState
    ) -> Any: ...

    def clear(
        self, orders: Any, world_state: WorldState
    ) -> Tuple[List[StateUpdateCommand], List[Dict[str, Any]]]: ...


def execute_tick(
    world_state: WorldState, decisions: TickDecisions, config: Any
) -> Tuple[List[StateUpdateCommand], List[Dict[str, Any]]]:
    """Orchestrator 可复用的执行函数签名（兼容现有 SimulationOrchestrator.run_tick）。

    返回值： (updates, logs/market_signals)
    """
    raise NotImplementedError

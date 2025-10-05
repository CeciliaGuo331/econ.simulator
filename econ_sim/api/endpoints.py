"""基于 FastAPI 暴露仿真引擎功能的接口定义。"""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..core.orchestrator import SimulationNotFoundError, SimulationOrchestrator
from ..data_access.models import (
    HouseholdState,
    TickDecisionOverrides,
    TickLogEntry,
    WorldState,
)

router = APIRouter(prefix="/simulations", tags=["simulations"])
_orchestrator = SimulationOrchestrator()


class SimulationCreateRequest(BaseModel):
    """创建仿真实例时可选传入自定义 ID 与配置路径。"""

    simulation_id: Optional[str] = None
    config_path: Optional[str] = None


class SimulationCreateResponse(BaseModel):
    """返回新建仿真实例的基础信息。"""

    simulation_id: str
    message: str
    current_tick: int
    current_day: int


class SimulationStatusResponse(BaseModel):
    """查询仿真实例状态时返回的运行信息。"""

    simulation_id: str
    status: str
    current_tick: int
    current_day: int


class RunTickRequest(BaseModel):
    """执行单个 Tick 时可选提供决策覆盖输入。"""

    decisions: Optional[TickDecisionOverrides] = None


class RunTickResponse(BaseModel):
    """执行单步仿真后的结果摘要与日志。"""

    message: str
    new_tick: int
    new_day: int
    logs: List[TickLogEntry]
    macro: dict


@router.post("", response_model=SimulationCreateResponse)
async def create_simulation(
    payload: SimulationCreateRequest,
) -> SimulationCreateResponse:
    """新建仿真实例并返回初始世界状态的关键信息。"""
    simulation_id = payload.simulation_id or str(uuid.uuid4())
    try:
        state = await _orchestrator.create_simulation(simulation_id)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc))

    return SimulationCreateResponse(
        simulation_id=simulation_id,
        message="Simulation created successfully.",
        current_tick=state.tick,
        current_day=state.day,
    )


@router.get("/{simulation_id}", response_model=SimulationStatusResponse)
async def get_simulation(simulation_id: str) -> SimulationStatusResponse:
    """获取指定仿真实例的当前 Tick、天数与运行状态。"""
    try:
        state = await _orchestrator.get_state(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return SimulationStatusResponse(
        simulation_id=simulation_id,
        status="running",
        current_tick=state.tick,
        current_day=state.day,
    )


@router.post("/{simulation_id}/run_tick", response_model=RunTickResponse)
async def run_tick(simulation_id: str, payload: RunTickRequest) -> RunTickResponse:
    """执行指定仿真实例的单个 Tick，并返回更新后的摘要。"""
    overrides = payload.decisions if payload and payload.decisions else None
    try:
        result = await _orchestrator.run_tick(simulation_id, overrides=overrides)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return RunTickResponse(
        message="Tick execution completed.",
        new_tick=result.world_state.tick,
        new_day=result.world_state.day,
        logs=result.logs,
        macro=result.world_state.macro.model_dump(),
    )


@router.get("/{simulation_id}/state/full", response_model=WorldState)
async def get_full_state(simulation_id: str) -> WorldState:
    """返回仿真实例的完整世界状态快照。"""
    try:
        return await _orchestrator.get_state(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class AgentStateList(BaseModel):
    """批量返回选定家户的状态信息。"""

    households: List[HouseholdState]


@router.get("/{simulation_id}/state/agents", response_model=AgentStateList)
async def get_agent_states(
    simulation_id: str, ids: Optional[str] = Query(default=None)
) -> AgentStateList:
    """按需筛选并返回家户状态列表。"""
    try:
        state = await _orchestrator.get_state(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if ids is None:
        households = list(state.households.values())
    else:
        id_list = [int(item.strip()) for item in ids.split(",") if item.strip()]
        households = [state.households[i] for i in id_list if i in state.households]

    return AgentStateList(households=households)

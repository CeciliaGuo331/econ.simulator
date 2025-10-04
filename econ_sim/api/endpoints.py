"""FastAPI endpoints exposing the simulation engine."""

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
    simulation_id: Optional[str] = None
    config_path: Optional[str] = None


class SimulationCreateResponse(BaseModel):
    simulation_id: str
    message: str
    current_tick: int
    current_day: int


class SimulationStatusResponse(BaseModel):
    simulation_id: str
    status: str
    current_tick: int
    current_day: int


class RunTickRequest(BaseModel):
    decisions: Optional[TickDecisionOverrides] = None


class RunTickResponse(BaseModel):
    message: str
    new_tick: int
    new_day: int
    logs: List[TickLogEntry]
    macro: dict


@router.post("", response_model=SimulationCreateResponse)
async def create_simulation(
    payload: SimulationCreateRequest,
) -> SimulationCreateResponse:
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
    try:
        return await _orchestrator.get_state(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class AgentStateList(BaseModel):
    households: List[HouseholdState]


@router.get("/{simulation_id}/state/agents", response_model=AgentStateList)
async def get_agent_states(
    simulation_id: str, ids: Optional[str] = Query(default=None)
) -> AgentStateList:
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

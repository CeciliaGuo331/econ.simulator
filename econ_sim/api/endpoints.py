"""基于 FastAPI 暴露仿真引擎功能及脚本管理的接口定义。"""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..auth import user_manager
from ..auth.user_manager import UserProfile
from ..core.orchestrator import (
    MissingAgentScriptsError,
    SimulationNotFoundError,
    SimulationOrchestrator,
    SimulationStateError,
)
from ..data_access.models import (
    AgentKind,
    HouseholdState,
    TickDecisionOverrides,
    TickLogEntry,
    WorldState,
)
from ..script_engine import script_registry
from ..script_engine.registry import ScriptExecutionError, ScriptMetadata
from ..utils.agents import resolve_agent_kind

router = APIRouter(prefix="/simulations", tags=["simulations"])
scripts_router = APIRouter(prefix="/scripts", tags=["scripts"])
_orchestrator = SimulationOrchestrator()


async def get_current_user(authorization: str = Header(...)) -> UserProfile:
    """根据 Access Token 获取当前登录用户。"""

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )
    profile = await user_manager.get_profile_by_token(token.strip())
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        )
    return profile


async def require_admin_user(
    user: UserProfile = Depends(get_current_user),
) -> UserProfile:
    """确保当前用户具备管理员权限。"""

    if user.user_type != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


class SimulationCreateRequest(BaseModel):
    """创建仿真实例时可选传入自定义 ID 与配置路径。"""

    simulation_id: Optional[str] = None
    config_path: Optional[str] = None
    user_id: Optional[str] = None


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


class SimulationDeleteResponse(BaseModel):
    """删除仿真实例后的反馈信息。"""

    simulation_id: str
    message: str
    participants_unlinked: int
    scripts_detached: int


class RunTickRequest(BaseModel):
    """执行单个 Tick 时可选提供决策覆盖输入。"""

    decisions: Optional[TickDecisionOverrides] = None


class SimulationParticipantRequest(BaseModel):
    """用于登记共享仿真会话参与者的请求体。"""

    user_id: str


class SimulationParticipantResponse(BaseModel):
    """返回指定仿真实例的参与者列表。"""

    participants: List[str]


class ScriptUploadRequest(BaseModel):
    """上传脚本时提供用户信息与脚本内容。"""

    user_id: Optional[str] = None
    code: str
    description: Optional[str] = None
    agent_kind: Optional[AgentKind] = None


class ScriptUploadResponse(BaseModel):
    """脚本上传成功后的反馈信息。"""

    script_id: str
    code_version: str
    simulation_id: Optional[str] = None
    message: str
    agent_kind: AgentKind
    entity_id: str


class ScriptAttachRequest(BaseModel):
    """挂载已有脚本到仿真实例的请求体。"""

    script_id: str


class ScriptListResponse(BaseModel):
    """返回当前仿真实例下的脚本元数据列表。"""

    scripts: List[ScriptMetadata]


class ScriptDeleteResponse(BaseModel):
    """删除脚本后的操作反馈。"""

    message: str


class ScriptLimitUpdateRequest(BaseModel):
    """管理员为仿真实例设置脚本上限时的请求体。"""

    max_scripts_per_user: Optional[int] = Field(
        default=None, ge=1, description="单个用户允许上传的脚本数量上限"
    )


class ScriptLimitResponse(BaseModel):
    """返回仿真实例当前的脚本上限设定。"""

    simulation_id: str
    max_scripts_per_user: Optional[int] = Field(default=None, ge=1)


class SimulationFeatureUpdateRequest(BaseModel):
    """更新仿真功能开关与参数时的请求体。"""

    household_shock_enabled: Optional[bool] = None
    household_shock_ability_std: Optional[float] = Field(default=None, ge=0.0)
    household_shock_asset_std: Optional[float] = Field(default=None, ge=0.0)
    household_shock_max_fraction: Optional[float] = Field(default=None, ge=0.0, le=0.9)


class SimulationFeatureResponse(BaseModel):
    """返回仿真实例当前的功能开关与参数。"""

    simulation_id: str
    household_shock_enabled: bool
    household_shock_ability_std: float
    household_shock_asset_std: float
    household_shock_max_fraction: float


class RunTickResponse(BaseModel):
    """执行单步仿真后的结果摘要与日志。"""

    message: str
    new_tick: int
    new_day: int
    logs: List[TickLogEntry]
    macro: dict


class RunDaysRequest(BaseModel):
    """批量执行多个天数时提交的请求体。"""

    days: int = Field(gt=0, description="需要自动推进的天数，必须为正整数")


class RunDaysResponse(BaseModel):
    """批量执行多个 Tick 后的结果摘要。"""

    message: str
    days_requested: int
    ticks_executed: int
    final_tick: int
    final_day: int
    logs: List[TickLogEntry]


class RunDayRequest(BaseModel):
    """执行单个仿真日时提交的请求体。"""

    ticks_per_day: Optional[int] = Field(
        default=None,
        gt=0,
        description="本次执行的 Tick 数量，缺省采用世界配置值",
    )


class RunDayResponse(BaseModel):
    """执行单日仿真后的结果摘要。"""

    message: str
    ticks_executed: int
    final_tick: int
    final_day: int
    logs: List[TickLogEntry]


class TickLogQueryParams(BaseModel):
    since_tick: Optional[int] = Field(default=None, ge=0)
    until_tick: Optional[int] = Field(default=None, ge=0)
    since_day: Optional[int] = Field(default=None, ge=0)
    until_day: Optional[int] = Field(default=None, ge=0)
    message: Optional[str] = None
    limit: Optional[int] = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class TickLogListResponse(BaseModel):
    simulation_id: str
    items: List[TickLogEntry]


@router.post("", response_model=SimulationCreateResponse)
async def create_simulation(
    payload: SimulationCreateRequest,
    admin: UserProfile = Depends(require_admin_user),
) -> SimulationCreateResponse:
    """新建仿真实例并返回初始世界状态的关键信息。"""
    simulation_id = payload.simulation_id or str(uuid.uuid4())
    try:
        state = await _orchestrator.create_simulation(simulation_id)
        if payload.user_id:
            await _orchestrator.register_participant(simulation_id, payload.user_id)
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


@router.delete("/{simulation_id}", response_model=SimulationDeleteResponse)
async def delete_simulation(
    simulation_id: str,
    admin: UserProfile = Depends(require_admin_user),
) -> SimulationDeleteResponse:
    """删除指定仿真实例，并解除与参与者和脚本的关联。"""

    try:
        result = await _orchestrator.delete_simulation(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    message = "Simulation deleted successfully."
    return SimulationDeleteResponse(
        simulation_id=simulation_id,
        message=message,
        participants_unlinked=result["participants_removed"],
        scripts_detached=result["scripts_detached"],
    )


@router.post("/{simulation_id}/run_tick", response_model=RunTickResponse)
async def run_tick(
    simulation_id: str,
    payload: RunTickRequest,
    admin: UserProfile = Depends(require_admin_user),
) -> RunTickResponse:
    """执行指定仿真实例的单个 Tick，并返回更新后的摘要。"""
    overrides = payload.decisions if payload and payload.decisions else None
    try:
        result = await _orchestrator.run_tick(simulation_id, overrides=overrides)
    except MissingAgentScriptsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return RunTickResponse(
        message="Tick execution completed.",
        new_tick=result.world_state.tick,
        new_day=result.world_state.day,
        logs=result.logs,
        macro=result.world_state.macro.model_dump(),
    )


@router.post("/{simulation_id}/run_days", response_model=RunDaysResponse)
async def run_days(
    simulation_id: str,
    payload: RunDaysRequest,
    admin: UserProfile = Depends(require_admin_user),
) -> RunDaysResponse:
    """按照指定天数自动执行多个 Tick。"""

    try:
        result = await _orchestrator.run_until_day(simulation_id, payload.days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except MissingAgentScriptsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except RuntimeError as exc:  # pragma: no cover - defensive guard
        raise HTTPException(status_code=500, detail=str(exc))

    return RunDaysResponse(
        message=f"Simulation advanced by {payload.days} day(s).",
        days_requested=payload.days,
        ticks_executed=result.ticks_executed,
        final_tick=result.world_state.tick,
        final_day=result.world_state.day,
        logs=result.logs,
    )


@router.post("/{simulation_id}/run_day", response_model=RunDayResponse)
async def run_day(
    simulation_id: str,
    payload: Optional[RunDayRequest] = None,
    admin: UserProfile = Depends(require_admin_user),
) -> RunDayResponse:
    """执行单个仿真日（按配置或指定 Tick 数量）。"""

    ticks_per_day = payload.ticks_per_day if payload else None
    try:
        result = await _orchestrator.run_day(simulation_id, ticks_per_day=ticks_per_day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except MissingAgentScriptsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return RunDayResponse(
        message="Simulation advanced by one day.",
        ticks_executed=result.ticks_executed,
        final_tick=result.world_state.tick,
        final_day=result.world_state.day,
        logs=result.logs,
    )


@router.get("/{simulation_id}/state/full", response_model=WorldState)
async def get_full_state(simulation_id: str) -> WorldState:
    """返回仿真实例的完整世界状态快照。"""
    try:
        return await _orchestrator.get_state(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{simulation_id}/history/tick_logs", response_model=TickLogListResponse)
async def get_tick_logs(
    simulation_id: str,
    since_tick: Optional[int] = Query(default=None, ge=0),
    until_tick: Optional[int] = Query(default=None, ge=0),
    since_day: Optional[int] = Query(default=None, ge=0),
    until_day: Optional[int] = Query(default=None, ge=0),
    message: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> TickLogListResponse:
    """查询历史 Tick 日志（若未配置 Postgres 历史持久化则返回空列表）。"""
    try:
        # Ensure simulation exists
        await _orchestrator.get_state(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    items = await _orchestrator.data_access.query_tick_logs(
        simulation_id,
        since_tick=since_tick,
        until_tick=until_tick,
        since_day=since_day,
        until_day=until_day,
        message=message,
        limit=limit,
        offset=offset,
    )
    return TickLogListResponse(simulation_id=simulation_id, items=items)


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


@router.post(
    "/{simulation_id}/participants", response_model=SimulationParticipantResponse
)
async def register_participant(
    simulation_id: str, payload: SimulationParticipantRequest
) -> SimulationParticipantResponse:
    """登记共享仿真实例的参与者信息。"""

    try:
        participants = await _orchestrator.register_participant(
            simulation_id, payload.user_id
        )
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc))
    return SimulationParticipantResponse(participants=participants)


@router.get(
    "/{simulation_id}/participants", response_model=SimulationParticipantResponse
)
async def list_participants(simulation_id: str) -> SimulationParticipantResponse:
    """查询当前仿真实例的参与者列表。"""

    try:
        participants = await _orchestrator.list_participants(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return SimulationParticipantResponse(participants=participants)


@router.put(
    "/{simulation_id}/settings/script_limit",
    response_model=ScriptLimitResponse,
)
async def update_script_limit(
    simulation_id: str,
    payload: ScriptLimitUpdateRequest,
    admin: UserProfile = Depends(require_admin_user),
) -> ScriptLimitResponse:
    """为指定仿真实例设置每位用户的脚本数量上限。"""

    try:
        applied = await _orchestrator.set_script_limit(
            simulation_id, payload.max_scripts_per_user
        )
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"仿真实例 {simulation_id} 已运行到 tick {exc.tick}，"
                "无法再调整脚本数量上限。"
            ),
        )
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return ScriptLimitResponse(
        simulation_id=simulation_id, max_scripts_per_user=applied
    )


@router.get(
    "/{simulation_id}/settings/script_limit",
    response_model=ScriptLimitResponse,
)
async def get_script_limit(
    simulation_id: str,
    admin: UserProfile = Depends(require_admin_user),
) -> ScriptLimitResponse:
    """查询指定仿真实例当前的脚本数量上限。"""

    try:
        limit = await _orchestrator.get_script_limit(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return ScriptLimitResponse(simulation_id=simulation_id, max_scripts_per_user=limit)


@router.put(
    "/{simulation_id}/settings/features",
    response_model=SimulationFeatureResponse,
)
async def update_simulation_features(
    simulation_id: str,
    payload: SimulationFeatureUpdateRequest,
    admin: UserProfile = Depends(require_admin_user),
) -> SimulationFeatureResponse:
    """更新指定仿真实例的功能开关与冲击参数。"""

    updates = payload.model_dump(exclude_none=True)

    try:
        state = await _orchestrator.update_simulation_features(simulation_id, **updates)
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"仿真实例 {simulation_id} 已运行到 tick {exc.tick}，"
                "无法再调整外生冲击配置。"
            ),
        )
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    features = state.features
    return SimulationFeatureResponse(
        simulation_id=simulation_id,
        household_shock_enabled=features.household_shock_enabled,
        household_shock_ability_std=features.household_shock_ability_std,
        household_shock_asset_std=features.household_shock_asset_std,
        household_shock_max_fraction=features.household_shock_max_fraction,
    )


@router.get(
    "/{simulation_id}/settings/features",
    response_model=SimulationFeatureResponse,
)
async def get_simulation_features(
    simulation_id: str,
    admin: UserProfile = Depends(require_admin_user),
) -> SimulationFeatureResponse:
    """查询仿真实例的功能开关配置。"""

    try:
        features = await _orchestrator.get_simulation_features(simulation_id)
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return SimulationFeatureResponse(
        simulation_id=simulation_id,
        household_shock_enabled=features.household_shock_enabled,
        household_shock_ability_std=features.household_shock_ability_std,
        household_shock_asset_std=features.household_shock_asset_std,
        household_shock_max_fraction=features.household_shock_max_fraction,
    )


@scripts_router.post("", response_model=ScriptUploadResponse)
async def upload_user_script(
    payload: ScriptUploadRequest,
    user: UserProfile = Depends(get_current_user),
) -> ScriptUploadResponse:
    """上传脚本到个人脚本库，稍后可挂载到任一仿真实例。"""

    if payload.user_id and payload.user_id != user.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot upload script for other users",
        )

    try:
        resolved_kind = resolve_agent_kind(
            user.user_type,
            payload.agent_kind,
            allow_override=user.user_type == "admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        metadata = await script_registry.register_script(
            simulation_id=None,
            user_id=user.email,
            script_code=payload.code,
            description=payload.description,
            agent_kind=resolved_kind,
        )
    except ScriptExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return ScriptUploadResponse(
        script_id=metadata.script_id,
        code_version=metadata.code_version,
        simulation_id=metadata.simulation_id,
        message="Script uploaded. Attach to a simulation when ready.",
        agent_kind=metadata.agent_kind,
        entity_id=metadata.entity_id,
    )


@scripts_router.get("", response_model=ScriptListResponse)
async def list_user_scripts(
    user: UserProfile = Depends(get_current_user),
) -> ScriptListResponse:
    """列出当前用户的所有脚本（含未挂载仿真）。"""

    scripts = await script_registry.list_user_scripts(user.email)
    return ScriptListResponse(scripts=scripts)


@router.post("/{simulation_id}/scripts", response_model=ScriptUploadResponse)
async def upload_script(
    simulation_id: str,
    payload: ScriptUploadRequest,
    user: UserProfile = Depends(get_current_user),
) -> ScriptUploadResponse:
    """上传并注册脚本，使其在 Tick 执行时参与决策。"""

    if payload.user_id and payload.user_id != user.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot upload script for other users",
        )

    try:
        resolved_kind = resolve_agent_kind(
            user.user_type,
            payload.agent_kind,
            allow_override=user.user_type == "admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        metadata = await _orchestrator.register_script_for_simulation(
            simulation_id=simulation_id,
            user_id=user.email,
            script_code=payload.code,
            description=payload.description,
            agent_kind=resolved_kind,
        )
        await _orchestrator.register_participant(simulation_id, user.email)
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"仿真实例 {simulation_id} 已运行到 tick {exc.tick}，"
                "仅在 tick 0 时允许挂载或上传脚本。"
            ),
        )
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScriptExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return ScriptUploadResponse(
        script_id=metadata.script_id,
        code_version=metadata.code_version,
        simulation_id=metadata.simulation_id,
        message="Script registered successfully.",
        agent_kind=metadata.agent_kind,
        entity_id=metadata.entity_id,
    )


@router.get("/{simulation_id}/scripts", response_model=ScriptListResponse)
async def list_scripts(simulation_id: str) -> ScriptListResponse:
    """返回当前仿真实例下的脚本列表。"""

    scripts = await script_registry.list_scripts(simulation_id)
    return ScriptListResponse(scripts=scripts)


@router.post("/{simulation_id}/scripts/attach", response_model=ScriptUploadResponse)
async def attach_script(
    simulation_id: str,
    payload: ScriptAttachRequest,
    user: UserProfile = Depends(get_current_user),
) -> ScriptUploadResponse:
    """将既有脚本挂载到指定仿真实例。"""

    try:
        metadata = await _orchestrator.attach_script_to_simulation(
            simulation_id=simulation_id,
            script_id=payload.script_id,
            user_id=user.email,
        )
        await _orchestrator.register_participant(simulation_id, user.email)
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"仿真实例 {simulation_id} 已运行到 tick {exc.tick}，"
                "仅在 tick 0 时允许挂载或上传脚本。"
            ),
        )
    except SimulationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScriptExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return ScriptUploadResponse(
        script_id=metadata.script_id,
        code_version=metadata.code_version,
        simulation_id=metadata.simulation_id,
        message="Script attached successfully.",
        agent_kind=metadata.agent_kind,
        entity_id=metadata.entity_id,
    )


@router.delete(
    "/{simulation_id}/scripts/{script_id}", response_model=ScriptDeleteResponse
)
async def delete_script(
    simulation_id: str,
    script_id: str,
    admin: UserProfile = Depends(require_admin_user),
) -> ScriptDeleteResponse:
    """从指定仿真实例中移除脚本。"""

    try:
        await _orchestrator.remove_script_from_simulation(simulation_id, script_id)
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"仿真实例 {simulation_id} 已运行到 tick {exc.tick}，"
                "仅在 tick 0 时允许删除挂载的脚本。"
            ),
        )
    except ScriptExecutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return ScriptDeleteResponse(message="Script removed.")

"""简单的服务器端渲染界面，提供登录、仪表盘和文档页。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ..auth import user_manager
from ..auth.user_manager import (
    AuthenticationError,
    PUBLIC_USER_TYPES,
    UserAlreadyExistsError,
)
from ..core.orchestrator import SimulationNotFoundError, SimulationOrchestrator
from ..script_engine import script_registry
from ..script_engine.registry import ScriptExecutionError
from .background import BackgroundJobManager, JobConflictError

router = APIRouter(prefix="/web", tags=["web"])

_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
_orchestrator = SimulationOrchestrator()
_background_jobs = BackgroundJobManager()

ROLE_GUIDES: Dict[str, Dict[str, Any]] = {
    "individual": {
        "title": "个人用户（individual）",
        "goal": "提升家庭消费能力与现金流，适时调整劳动供给与储蓄率。",
        "data_points": [
            Markup(
                "使用 <code>/simulations/{simulation_id}/state/agents?ids=...</code> 查看个人资产与就业状态。"
            ),
            "仪表盘“角色视角数据”板块展示家户的现金、消费与工资信息，可用于快速验证策略效果。",
        ],
        "api_notes": [
            Markup(
                "<code>POST /simulations/{simulation_id}/scripts</code> 上传或更新家户策略脚本。"
            ),
            Markup(
                "<code>POST /simulations/{simulation_id}/run_tick</code> 携带 <code>decisions.households</code> 覆盖推动仿真。"
            ),
        ],
    },
    "firm": {
        "title": "企业（firm）",
        "goal": "平衡库存与销售，动态调整价格、产量及招聘计划。",
        "data_points": [
            Markup(
                "关注 <code>/simulations/{simulation_id}/state/full</code> 中的 <code>firm</code>、<code>macro</code> 数据（库存、价格、GDP）。"
            ),
            "仪表盘脚本列表可了解其他参与者的企业策略，评估竞争环境。",
        ],
        "api_notes": [
            Markup(
                "<code>POST /simulations/{simulation_id}/run_tick</code> 中的 <code>decisions.firm</code> 字段可设置新一轮价格与产量。"
            ),
            Markup(
                "<code>GET /simulations/{simulation_id}/scripts</code> 查看或导出当前脚本。"
            ),
        ],
    },
    "government": {
        "title": "政府（government）",
        "goal": "稳定就业与税收，合理设置税率、岗位数量和转移支付。",
        "data_points": [
            "重点监控宏观指标中的失业率 (<code>macro.unemployment_rate</code>) 与财政余额。",
            "家户列表的就业状态可帮助评估公共岗位政策的效果。",
        ],
        "api_notes": [
            Markup(
                "<code>POST /simulations/{simulation_id}/run_tick</code> 的 <code>decisions.government</code> 字段用于更新税率与转移预算。"
            ),
            Markup(
                "<code>GET /simulations/{simulation_id}/state/full</code> 用于获取财政相关数据。"
            ),
        ],
    },
    "commercial_bank": {
        "title": "商业银行（commercial_bank）",
        "goal": "管理存贷款利差，控制信贷配额并保持资金安全。",
        "data_points": [
            Markup(
                "查看 <code>bank.balance_sheet</code> 中的存款、贷款余额以及 <code>approved_loans</code> 列表。"
            ),
            "关注宏观通胀率与央行政策，以调整利率策略。",
        ],
        "api_notes": [
            Markup(
                "<code>POST /simulations/{simulation_id}/run_tick</code> 的 <code>decisions.bank</code> 字段可设置存贷款利率与信贷供给。"
            ),
            Markup(
                "<code>GET /simulations/{simulation_id}/state/full</code> 查询银行资产负债表。"
            ),
        ],
    },
    "central_bank": {
        "title": "中央银行（central_bank）",
        "goal": "调节政策利率与准备金率，使通胀与失业率围绕目标值波动。",
        "data_points": [
            "重点跟踪 <code>macro.inflation</code> 与 <code>macro.unemployment_rate</code>。",
            "结合商业银行利率决策判断政策传导效果。",
        ],
        "api_notes": [
            Markup(
                "<code>POST /simulations/{simulation_id}/run_tick</code> 的 <code>decisions.central_bank</code> 字段用于设定政策利率与准备金率。"
            ),
            Markup(
                "<code>GET /simulations/{simulation_id}</code> 快速查看当前 Tick/Day。"
            ),
        ],
    },
    "admin": {
        "title": "管理员（admin）",
        "goal": "监控仿真运行状况、维护脚本安全并协助排查问题。",
        "data_points": [
            Markup(
                "通过 <code>/simulations/{simulation_id}/state/full</code> 导出完整世界状态，结合仪表盘全量视图巡检。"
            ),
            "脚本列表可帮助识别潜在问题策略并与参与者沟通。",
        ],
        "api_notes": [
            Markup(
                "<code>DELETE /simulations/{simulation_id}/scripts/{script_id}</code> 移除违规脚本。"
            ),
            "管理员账号仅用于监控，前端已禁止上传脚本。",
        ],
        "notes": ["请在部署后尽快修改默认管理员密码。"],
    },
}


def _get_session_user(request: Request) -> Optional[Dict[str, Any]]:
    session_user = request.session.get("user")
    if session_user and "email" in session_user:
        return session_user
    return None


async def _require_session_user(request: Request) -> Dict[str, Any]:
    session_user = _get_session_user(request)
    if not session_user:
        raise HTTPException(status_code=307, detail="login required")
    return session_user


async def _require_admin_user(request: Request) -> Dict[str, Any]:
    session_user = await _require_session_user(request)
    if session_user.get("user_type") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return session_user


async def _load_world_state(
    simulation_id: str, *, allow_create: bool
) -> Dict[str, Any]:
    if allow_create:
        state = await _orchestrator.create_simulation(simulation_id)
    else:
        state = await _orchestrator.get_state(simulation_id)
    return state.model_dump(mode="json")


def _redirect_to_dashboard(
    simulation_id: str,
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> RedirectResponse:
    params = {"simulation_id": simulation_id}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    query = urlencode(params)
    return RedirectResponse(url=f"/web/dashboard?{query}", status_code=303)


def _should_return_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        return True
    requested_with = (request.headers.get("x-requested-with") or "").lower()
    return requested_with in {"fetch", "xmlhttprequest"}


def _async_response(
    request: Request,
    simulation_id: str,
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    status_code: int = 200,
):
    if _should_return_json(request):
        payload: Dict[str, Any] = {"simulation_id": simulation_id}
        if message:
            payload["message"] = message
        if error:
            payload["error"] = error
            if status_code < 400:
                status_code = 400
        if extra:
            payload.update(extra)
        return JSONResponse(payload, status_code=status_code)

    return _redirect_to_dashboard(simulation_id, message=message, error=error)


def _format_tick_progress_message(
    simulation_id: str,
    *,
    tick: int,
    day: int,
    ticks_executed: int,
) -> str:
    return (
        f"仿真实例 {simulation_id} 当前 Tick {tick} (Day {day})，"
        f"本次执行 {ticks_executed} 个 Tick。"
    )


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    if _get_session_user(request):
        return RedirectResponse(url="/web/dashboard", status_code=303)
    return RedirectResponse(url="/web/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, message: Optional[str] = None) -> HTMLResponse:
    return _templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "message": message},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submission(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse:
    try:
        token = await user_manager.authenticate_user(email, password)
        profile = await user_manager.get_profile(email)
        if profile is None:
            raise AuthenticationError("Profile not found")
    except AuthenticationError:
        return _templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "邮箱或密码错误，请重试。",
                "message": None,
            },
            status_code=401,
        )

    request.session["user"] = {
        "email": profile.email,
        "token": token,
        "user_type": profile.user_type,
    }
    return RedirectResponse(url="/web/dashboard", status_code=303)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.pop("user", None)
    return RedirectResponse(url="/web/login", status_code=303)


def _extract_view_data(world_state: Dict[str, Any], user_type: str) -> Dict[str, Any]:
    if user_type == "individual":
        households = list(world_state.get("households", {}).values())[:5]
        return {"households": households}
    if user_type == "firm":
        return {"firm": world_state.get("firm")}
    if user_type == "government":
        return {"government": world_state.get("government")}
    if user_type == "commercial_bank":
        return {"bank": world_state.get("bank")}
    if user_type == "central_bank":
        return {"central_bank": world_state.get("central_bank")}
    return {"world": world_state}


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Dict[str, Any] = Depends(_require_session_user),
    simulation_id: str = "",
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> HTMLResponse:
    allow_create = user["user_type"] == "admin"
    all_simulations = await _orchestrator.list_simulations()

    normalized_id = (simulation_id or "").strip()
    if not normalized_id:
        if all_simulations:
            normalized_id = all_simulations[0]
        else:
            normalized_id = ""
    simulation_id = normalized_id

    if not allow_create and not simulation_id:
        friendly_message = (
            message or "当前没有可加入的仿真实例，请稍后再试或联系管理员创建新实例。"
        )
        return _templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "scripts": [],
                "context": {},
                "error": None,
                "message": friendly_message,
                "all_simulations": all_simulations,
                "all_users": [],
                "all_scripts": [],
                "scripts_by_user": {},
            },
            status_code=200,
        )

    if allow_create and not simulation_id:
        user_profiles = await user_manager.list_users()
        all_scripts = script_registry.list_all_scripts()
        scripts_by_user: Dict[str, List] = {}
        for metadata in all_scripts:
            scripts_by_user.setdefault(metadata.user_id, []).append(metadata)
        script_counts = {email: len(items) for email, items in scripts_by_user.items()}
        all_users = [
            {
                "email": profile.email,
                "created_at": profile.created_at,
                "user_type": profile.user_type,
                "script_count": script_counts.get(profile.email, 0),
            }
            for profile in user_profiles
        ]

        return _templates.TemplateResponse(
            "admin_dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "scripts": [],
                "context": {"world": {}},
                "error": error,
                "message": message,
                "all_simulations": all_simulations,
                "all_users": all_users,
                "all_scripts": all_scripts,
                "scripts_by_user": scripts_by_user,
            },
        )

    try:
        world_state = await _load_world_state(simulation_id, allow_create=False)
    except SimulationNotFoundError:
        template_name = "admin_dashboard.html" if allow_create else "dashboard.html"
        context: Dict[str, Any] = {"world": {}} if allow_create else {}
        friendly_error = error
        friendly_message = message
        resolved_simulation_id = simulation_id
        if not allow_create:
            friendly_error = None
            friendly_message = friendly_message or (
                "当前仿真实例不可用，您可以通过下方列表选择其他实例加入。"
                if all_simulations
                else "当前没有加入仿真实例，可联系管理员或稍后再试。"
            )
            resolved_simulation_id = ""
        else:
            friendly_error = (
                friendly_error or "仿真实例不存在，请联系管理员创建后再访问。"
            )
        simulation_id = resolved_simulation_id
        return _templates.TemplateResponse(
            template_name,
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "scripts": (
                    script_registry.list_scripts(simulation_id) if simulation_id else []
                ),
                "context": context,
                "error": friendly_error,
                "message": friendly_message,
                "all_simulations": all_simulations,
                "all_users": [],
                "all_scripts": [],
                "scripts_by_user": {},
            },
            status_code=404,
        )

    scripts = script_registry.list_scripts(simulation_id) if simulation_id else []
    context = _extract_view_data(world_state, user["user_type"])
    template_name = "dashboard.html"
    all_users: List[Dict[str, Any]] = []
    all_scripts = []
    scripts_by_user: Dict[str, List] = {}

    if allow_create:
        template_name = "admin_dashboard.html"
        context["world"] = world_state
        user_profiles = await user_manager.list_users()
        all_scripts = script_registry.list_all_scripts()
        for metadata in all_scripts:
            scripts_by_user.setdefault(metadata.user_id, []).append(metadata)
        script_counts = {email: len(items) for email, items in scripts_by_user.items()}
        all_users = [
            {
                "email": profile.email,
                "created_at": profile.created_at,
                "user_type": profile.user_type,
                "script_count": script_counts.get(profile.email, 0),
            }
            for profile in user_profiles
        ]

    return _templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "user": user,
            "simulation_id": simulation_id,
            "scripts": scripts,
            "context": context,
            "error": error,
            "message": message,
            "all_simulations": all_simulations,
            "all_users": all_users,
            "all_scripts": all_scripts,
            "scripts_by_user": scripts_by_user,
        },
    )


@router.post("/simulations/join")
async def join_simulation(
    user: Dict[str, Any] = Depends(_require_session_user),
    simulation_id: str = Form(...),
) -> RedirectResponse:
    if user.get("user_type") == "admin":
        return _redirect_to_dashboard(
            simulation_id,
            error="管理员账号无需加入仿真实例。",
        )

    target = simulation_id.strip()
    if not target:
        return _redirect_to_dashboard(
            "",
            error="请选择仿真实例后再加入。",
        )

    try:
        await _orchestrator.register_participant(target, user["email"])
    except SimulationNotFoundError:
        return _redirect_to_dashboard(
            "",
            error=f"仿真实例 {target} 不存在，请刷新列表后重试。",
        )

    return _redirect_to_dashboard(
        target,
        message=f"已加入仿真实例 {target}。",
    )


@router.post("/admin/simulations/create")
async def admin_create_simulation(
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(""),
    current_simulation_id: str = Form("default-simulation"),
) -> RedirectResponse:
    desired_id = simulation_id.strip()
    generated = False
    if not desired_id:
        desired_id = f"sim-{uuid.uuid4().hex[:8]}"
        generated = True
    try:
        await _orchestrator.create_simulation(desired_id)
    except Exception as exc:  # pragma: no cover - defensive
        fallback = current_simulation_id or "default-simulation"
        return _redirect_to_dashboard(fallback, error=f"创建仿真实例失败: {exc}")

    note = f"已创建仿真实例 {desired_id}."
    if generated:
        note += " (自动生成 ID)"
    return _redirect_to_dashboard(desired_id, message=note)


@router.post("/admin/simulations/run")
async def admin_run_tick(
    request: Request,
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(""),
    current_simulation_id: str = Form("default-simulation"),
):
    target = simulation_id.strip() or current_simulation_id or "default-simulation"
    try:
        result = await _orchestrator.run_tick(target)
    except SimulationNotFoundError:
        return _async_response(
            request,
            current_simulation_id or target,
            error=f"仿真实例 {target} 不存在，无法执行 Tick。",
        )

    note = _format_tick_progress_message(
        target,
        tick=result.world_state.tick,
        day=result.world_state.day,
        ticks_executed=1,
    )
    extra = {
        "tick": result.world_state.tick,
        "day": result.world_state.day,
        "ticks_executed": 1,
    }
    return _async_response(request, target, message=note, extra=extra)


@router.post("/admin/simulations/run_days")
async def admin_run_days(
    request: Request,
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(...),
    days: str = Form(...),
    current_simulation_id: str = Form("default-simulation"),
):
    target = simulation_id.strip() or current_simulation_id or "default-simulation"
    try:
        days_required = int(days)
    except (TypeError, ValueError):
        return _async_response(
            request,
            target,
            error="请输入合法的天数（正整数）。",
        )

    if days_required <= 0:
        return _async_response(request, target, error="天数必须大于 0。")

    try:
        await _orchestrator.create_simulation(target)
    except SimulationNotFoundError:
        return _async_response(
            request,
            current_simulation_id or target,
            error=f"仿真实例 {target} 不存在，无法执行自动运行。",
        )

    async def _job_factory() -> Dict[str, Any]:
        try:
            result = await _orchestrator.run_until_day(target, days_required)
        except SimulationNotFoundError:
            raise
        note = _format_tick_progress_message(
            target,
            tick=result.world_state.tick,
            day=result.world_state.day,
            ticks_executed=result.ticks_executed,
        )
        return {
            "message": note,
            "extra": {
                "tick": result.world_state.tick,
                "day": result.world_state.day,
                "ticks_executed": result.ticks_executed,
            },
        }

    try:
        job = await _background_jobs.enqueue(target, "run_days", _job_factory)
    except JobConflictError as exc:
        existing = await _background_jobs.get(exc.existing_job_id)
        extra = {}
        if existing:
            extra["job_id"] = existing.job_id
            extra["job_status"] = existing.status
            extra["job_action"] = existing.action
            if existing.message:
                extra["job_message"] = existing.message
        return _async_response(
            request,
            target,
            error=f"仿真实例 {target} 已有正在执行的自动运行任务。",
            extra=extra or None,
        )

    kickoff_message = (
        f"仿真实例 {target} 的自动执行任务已启动（Job: {job.job_id[:8]}…）。"
    )
    return _async_response(
        request,
        target,
        message=kickoff_message,
        extra={
            "job_id": job.job_id,
            "job_status": job.status,
            "job_action": job.action,
        },
    )


@router.post("/admin/simulations/reset")
async def admin_reset_simulation(
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(...),
) -> RedirectResponse:
    target = simulation_id.strip()
    if not target:
        return _redirect_to_dashboard("default-simulation", error="请指定仿真实例 ID。")

    try:
        state = await _orchestrator.reset_simulation(target)
    except SimulationNotFoundError:
        return _redirect_to_dashboard(
            target,
            error=f"仿真实例 {target} 不存在，无法重置。",
        )

    note = f"仿真实例 {target} 已重置至 Tick {state.tick}。"
    return _redirect_to_dashboard(target, message=note)


@router.get("/admin/jobs/{job_id}")
async def admin_job_status(
    job_id: str,
    user: Dict[str, Any] = Depends(_require_admin_user),
):
    job = await _background_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(job.as_dict())


@router.post("/admin/simulations/delete")
async def admin_delete_simulation(
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(...),
    current_simulation_id: str = Form("default-simulation"),
) -> RedirectResponse:
    target = simulation_id.strip()
    if not target:
        return _redirect_to_dashboard(
            current_simulation_id or "default-simulation",
            error="请指定仿真实例 ID。",
        )

    try:
        result = await _orchestrator.delete_simulation(target)
    except SimulationNotFoundError:
        return _redirect_to_dashboard(
            current_simulation_id or "default-simulation",
            error=f"仿真实例 {target} 不存在或已删除。",
        )

    message = f"仿真实例 {target} 已删除。"
    if result["participants_removed"]:
        message += f" 解除 {result['participants_removed']} 个参与者关联。"
    if result["scripts_detached"]:
        message += f" 移除 {result['scripts_detached']} 个脚本关联。"

    redirect_target = current_simulation_id or "default-simulation"
    if target == redirect_target:
        remaining = await _orchestrator.list_simulations()
        redirect_target = remaining[0] if remaining else "default-simulation"

    return _redirect_to_dashboard(redirect_target, message=message)


@router.post("/admin/scripts/delete")
async def admin_delete_script(
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(...),
    script_id: str = Form(...),
    current_simulation_id: str = Form("default-simulation"),
) -> RedirectResponse:
    target = simulation_id.strip()
    redirect_target = current_simulation_id or target or "default-simulation"
    if not target:
        return _redirect_to_dashboard(
            redirect_target,
            error="请提供脚本所属的仿真实例。",
        )
    try:
        script_registry.remove_script(target, script_id)
    except ScriptExecutionError as exc:
        return _redirect_to_dashboard(redirect_target, error=str(exc))

    note = f"已删除仿真实例 {target} 下的脚本 {script_id}。"
    return _redirect_to_dashboard(redirect_target, message=note)


@router.post("/admin/users/delete")
async def admin_delete_user(
    user: Dict[str, Any] = Depends(_require_admin_user),
    email: str = Form(...),
    current_simulation_id: str = Form("default-simulation"),
) -> RedirectResponse:
    normalized = email.strip().lower()
    redirect_target = current_simulation_id or "default-simulation"
    if not normalized:
        return _redirect_to_dashboard(
            redirect_target,
            error="请提供需要删除的用户邮箱。",
        )
    try:
        await user_manager.delete_user(normalized)
    except ValueError as exc:
        return _redirect_to_dashboard(redirect_target, error=str(exc))

    removed = script_registry.remove_scripts_by_user(normalized)
    note = f"用户 {normalized} 已删除。"
    if removed:
        note += f" 同时移除 {removed} 个脚本。"
    return _redirect_to_dashboard(redirect_target, message=note)


@router.post("/scripts", response_class=HTMLResponse)
async def upload_script(
    request: Request,
    user: Dict[str, Any] = Depends(_require_session_user),
    simulation_id: str = Form(...),
    description: str = Form(""),
    code: str = Form(...),
) -> HTMLResponse:
    if user["user_type"] == "admin":
        return _templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "scripts": script_registry.list_scripts(simulation_id),
                "context": {},
                "error": "管理员账号不能上传脚本。",
            },
            status_code=403,
        )

    try:
        await _orchestrator.register_participant(simulation_id, user["email"])
        script_registry.register_script(
            simulation_id=simulation_id,
            user_id=user["email"],
            script_code=code,
            description=description or None,
        )
    except SimulationNotFoundError:
        return _templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "scripts": [],
                "context": {},
                "error": "仿真实例不存在，请联系管理员创建后再上传脚本。",
            },
            status_code=404,
        )
    except ScriptExecutionError as exc:
        world_state = (await _orchestrator.get_state(simulation_id)).model_dump(
            mode="json"
        )
        context = _extract_view_data(world_state, user["user_type"])
        return _templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "scripts": script_registry.list_scripts(simulation_id),
                "context": context,
                "error": str(exc),
            },
            status_code=400,
        )

    return RedirectResponse(
        url=f"/web/dashboard?simulation_id={simulation_id}", status_code=303
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "error": None,
            "email": "",
            "user_type": "individual",
            "user_types": sorted(PUBLIC_USER_TYPES),
        },
    )


@router.post("/register", response_class=HTMLResponse)
async def register_submission(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    user_type: str = Form(...),
) -> HTMLResponse:
    if password != confirm_password:
        return _templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": "两次输入的密码不一致，请重新输入。",
                "email": email,
                "user_type": user_type,
                "user_types": sorted(PUBLIC_USER_TYPES),
            },
            status_code=400,
        )

    try:
        await user_manager.register_user(email, password, user_type)
    except UserAlreadyExistsError:
        return _templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": "该邮箱已注册，试试直接登录或换一个邮箱。",
                "email": email,
                "user_type": user_type,
                "user_types": sorted(PUBLIC_USER_TYPES),
            },
            status_code=409,
        )
    except ValueError as exc:
        return _templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": str(exc),
                "email": email,
                "user_type": user_type,
                "user_types": sorted(PUBLIC_USER_TYPES),
            },
            status_code=400,
        )
    except Exception:
        return _templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": "注册失败，请稍后再试。",
                "email": email,
                "user_type": user_type,
                "user_types": sorted(PUBLIC_USER_TYPES),
            },
            status_code=500,
        )

    return RedirectResponse(
        url="/web/login?message=注册成功，请登录。", status_code=303
    )


@router.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request) -> HTMLResponse:
    session_user = _get_session_user(request)
    user_type = session_user.get("user_type") if session_user else None
    guides: list[Dict[str, Any]] = []
    if user_type and user_type in ROLE_GUIDES:
        guides = [ROLE_GUIDES[user_type]]

    return _templates.TemplateResponse(
        "docs.html",
        {
            "request": request,
            "role_guides": guides,
            "active_role": user_type,
        },
    )

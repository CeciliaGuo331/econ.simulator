"""简单的服务器端渲染界面，提供登录、仪表盘和文档页。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ..auth import user_manager
from ..auth.user_manager import (
    AuthenticationError,
    PUBLIC_USER_TYPES,
    UserAlreadyExistsError,
)
from ..core.orchestrator import SimulationOrchestrator
from ..script_engine import script_registry
from ..script_engine.registry import ScriptExecutionError

router = APIRouter(prefix="/web", tags=["web"])

_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
_orchestrator = SimulationOrchestrator()

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


async def _load_world_state(simulation_id: str):
    state = await _orchestrator.create_simulation(simulation_id)
    return state.model_dump(mode="json")


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
    simulation_id: str = "default-simulation",
) -> HTMLResponse:
    world_state = await _load_world_state(simulation_id)
    scripts = script_registry.list_scripts(simulation_id)
    context = _extract_view_data(world_state, user["user_type"])
    template_name = "dashboard.html"
    if user["user_type"] == "admin":
        template_name = "admin_dashboard.html"
        context["world"] = world_state
    return _templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "user": user,
            "simulation_id": simulation_id,
            "scripts": scripts,
            "context": context,
        },
    )


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
    except ScriptExecutionError as exc:
        world_state = await _load_world_state(simulation_id)
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

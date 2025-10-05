"""简单的服务器端渲染界面，提供登录、仪表盘和文档页。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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
    return _templates.TemplateResponse(
        "docs.html",
        {
            "request": request,
        },
    )

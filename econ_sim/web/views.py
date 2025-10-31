"""简单的服务器端渲染界面，提供登录、仪表盘和文档页。"""

from __future__ import annotations

import io
import asyncio
import json
import logging
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode

import markdown
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
    PlainTextResponse,
)
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ..auth import user_manager
from ..auth.user_manager import (
    AuthenticationError,
    PUBLIC_USER_TYPES,
    UserAlreadyExistsError,
)
from ..core.orchestrator import (
    SimulationNotFoundError,
    SimulationOrchestrator,
    SimulationStateError,
)
from ..data_access.models import AgentKind
from ..script_engine import script_registry
from ..script_engine.registry import ScriptExecutionError
from ..utils.agents import get_default_agent_kind, resolve_agent_kind
from ..utils.settings import get_world_config
from .background import BackgroundJobManager, JobConflictError
from .background import BackgroundJobManager, JobConflictError
import mimetypes
import re

router = APIRouter(prefix="/web", tags=["web"])

logger = logging.getLogger(__name__)

_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
# These are created during FastAPI application startup and injected by
# `econ_sim.main`. Keep the names here for backward compatibility with
# code and tests that import them directly (e.g. `from econ_sim.web.views import _orchestrator`).
_orchestrator: Optional[SimulationOrchestrator] = None
_background_jobs: Optional[BackgroundJobManager] = None

_DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs" / "user_strategies"
_STATIC_ROOT = Path(__file__).resolve().parent / "static"
_AVATAR_DIR = _STATIC_ROOT / "avatars"

ROLE_DOC_FILES: Dict[str, Dict[str, str]] = {
    "admin": {"title": "管理员操作指南", "filename": "admin.md"},
    "individual": {"title": "家户策略指南", "filename": "household.md"},
    "firm": {"title": "企业策略指南", "filename": "firm.md"},
    "government": {"title": "政府策略指南", "filename": "government.md"},
    "commercial_bank": {"title": "商业银行策略指南", "filename": "bank.md"},
    "central_bank": {"title": "中央银行策略指南", "filename": "central_bank.md"},
}


def _get_ticks_per_day_default() -> int:
    """获取默认的 ticks_per_day。

    优先从 orchestrator.config 读取；当测试使用 DummyOrchestrator 无该属性时，
    回退到全局 world 配置。
    """
    try:
        cfg = getattr(_orchestrator, "config", None)
        if cfg and getattr(cfg, "simulation", None):
            return int(cfg.simulation.ticks_per_day)
    except Exception:  # pragma: no cover - 防御性兜底
        pass
    return int(get_world_config().simulation.ticks_per_day)


def _extract_tick_from_state(state: Any) -> Optional[int]:
    """从世界状态对象或字典中提取 tick 值。

    兼容 tests 中的 DummyWorldState（仅提供 model_dump）。
    """
    # object-like with attribute
    tick = getattr(state, "tick", None)
    if isinstance(tick, int):
        return tick
    # dict-like
    if isinstance(state, dict):
        raw_tick = state.get("tick")
        return int(raw_tick) if isinstance(raw_tick, int) else None
    # pydantic-like with model_dump
    try:
        if hasattr(state, "model_dump"):
            dumped = state.model_dump(mode="json")  # type: ignore[attr-defined]
            if isinstance(dumped, dict):
                raw_tick = dumped.get("tick")
                return int(raw_tick) if isinstance(raw_tick, int) else None
    except Exception:  # pragma: no cover - 防御性兜底
        return None
    return None


@lru_cache(maxsize=32)
def _render_markdown(relative_path: str) -> Markup:
    doc_path = _DOCS_ROOT / relative_path
    try:
        raw = doc_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("指定的文档不存在: %s", doc_path)
        return Markup("<p class='text-danger'>对应的文档暂时不可用，请联系管理员。</p>")

    html = markdown.markdown(
        raw,
        extensions=["fenced_code", "codehilite", "tables", "toc"],
        extension_configs={
            "codehilite": {"guess_lang": False, "linenums": False},
        },
    )
    return Markup(html)


def _format_logs_for_download(entries: List[Any]) -> str:
    if not entries:
        return "尚无可用日志。\n"

    lines: List[str] = []
    for entry in entries:
        tick = getattr(entry, "tick", "?")
        day = getattr(entry, "day", "?")
        message = getattr(entry, "message", "") or ""
        line = f"Day {day} | Tick {tick} | {message}"
        context = getattr(entry, "context", None)
        if context:
            try:
                context_str = json.dumps(context, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                context_str = str(context)
            if context_str:
                line += f" | context={context_str}"
        lines.append(line)

    return "\n".join(lines) + "\n"


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


def _as_dict(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "model_dump"):
        try:
            return payload.model_dump(mode="json")  # type: ignore[no-any-return]
        except Exception:  # pragma: no cover - defensive
            return dict(payload) if hasattr(payload, "items") else {}
    if hasattr(payload, "items"):
        return dict(payload)
    return {}


def _prepare_world_for_template(raw_world: Dict[str, Any]) -> Dict[str, Any]:
    world = {**raw_world}
    world.setdefault("households", {})
    world.setdefault("household_shocks", {})
    world.setdefault("features", {})

    # Sort households by numeric ID if they are dict
    households = world.get("households")
    if isinstance(households, dict) and households:
        # Use an alphanumeric key that treats digit runs as integers so
        # that keys like "1", "2", "10" sort numerically and mixed
        # keys like "house-2" / "house-10" sort intuitively.
        def _alphanum_key(s: object):
            # split into digit and non-digit parts
            parts = re.split(r"(\d+)", str(s))
            key_parts = []
            for p in parts:
                if p.isdigit():
                    try:
                        key_parts.append(int(p))
                    except Exception:
                        key_parts.append(p)
                else:
                    key_parts.append(p.lower())
            return key_parts

        try:
            sorted_items = sorted(
                households.items(), key=lambda item: _alphanum_key(item[0])
            )
            # Keep a dict for backwards-compatibility, but also expose an
            # ordered list of (id, info) pairs so templates can reliably
            # iterate in the numeric-aware order.
            sorted_households = dict(sorted_items)
            world["households"] = sorted_households
            world["households_list"] = sorted_items
        except Exception:
            # If anything unexpected happens, keep original order
            pass

    for agent_key in ("firm", "bank", "government", "central_bank"):
        agent_raw = world.get(agent_key)
        agent_dict = _as_dict(agent_raw)
        if not agent_dict:
            agent_dict = {}
        agent_dict.setdefault("balance_sheet", {})
        if agent_key in {"firm", "government"}:
            agent_dict.setdefault("employees", [])
        if agent_key == "bank":
            agent_dict.setdefault("approved_loans", {})
        world[agent_key] = agent_dict

    return world


def _safe_average(values: Iterable[Any]) -> Optional[float]:
    numeric: List[float] = []
    for item in values:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            numeric.append(float(item))
    if not numeric:
        return None
    return sum(numeric) / len(numeric)


def _summarize_households_data(raw_households: Any) -> Dict[str, Optional[float]]:
    if not raw_households:
        return {
            "count": 0,
            "avg_cash": None,
            "avg_deposits": None,
            "avg_loans": None,
            "avg_wage_income": None,
            "avg_last_consumption": None,
            "employment_rate": None,
        }

    if isinstance(raw_households, dict):
        entries = list(raw_households.values())
    elif isinstance(raw_households, list):
        entries = raw_households
    else:
        entries = []

    if not entries:
        return {
            "count": 0,
            "avg_cash": None,
            "avg_deposits": None,
            "avg_loans": None,
            "avg_wage_income": None,
            "avg_last_consumption": None,
            "employment_rate": None,
        }

    cash_values: List[float] = []
    deposit_values: List[float] = []
    loan_values: List[float] = []
    wage_values: List[float] = []
    consumption_values: List[float] = []
    employed = 0

    for entry in entries:
        data = _as_dict(entry)
        sheet = _as_dict(data.get("balance_sheet"))
        cash = sheet.get("cash")
        deposits = sheet.get("deposits")
        loans = sheet.get("loans")
        wage = data.get("wage_income")
        consumption = data.get("last_consumption")
        employment_status = str(data.get("employment_status", "")).lower()

        if isinstance(cash, (int, float)):
            cash_values.append(float(cash))
        if isinstance(deposits, (int, float)):
            deposit_values.append(float(deposits))
        if isinstance(loans, (int, float)):
            loan_values.append(float(loans))
        if isinstance(wage, (int, float)):
            wage_values.append(float(wage))
        if isinstance(consumption, (int, float)):
            consumption_values.append(float(consumption))
        if employment_status.startswith("employed"):
            employed += 1

    total = len(entries)
    employment_rate: Optional[float]
    if total:
        employment_rate = employed / total
    else:
        employment_rate = None

    return {
        "count": total,
        "avg_cash": _safe_average(cash_values),
        "avg_deposits": _safe_average(deposit_values),
        "avg_loans": _safe_average(loan_values),
        "avg_wage_income": _safe_average(wage_values),
        "avg_last_consumption": _safe_average(consumption_values),
        "employment_rate": employment_rate,
    }


def _table_row(label: str, value: Any, *, is_int: bool = False) -> tuple[Any, ...]:
    return (label, value, is_int)


_PENDING_ENTITY_LABEL = "待自动分配"


def _default_script_form_defaults(
    user_type: str,
    *,
    description: str = "",
    resolved_kind: Optional[AgentKind] = None,
) -> Dict[str, str]:
    defaults: Dict[str, str] = {}
    defaults["description"] = description
    effective_kind = resolved_kind or get_default_agent_kind(user_type)
    if effective_kind is not None:
        defaults["resolved_agent_kind"] = effective_kind.value
    return defaults


def _build_entity_display_map(*script_groups: Iterable) -> Dict[str, str]:
    display: Dict[str, str] = {}
    for scripts in script_groups:
        if not scripts:
            continue
        for script in scripts:
            script_id = getattr(script, "script_id", None)
            if not script_id:
                continue
            entity_id = getattr(script, "entity_id", "")
            if script_registry.is_placeholder_entity_id(entity_id):
                display[script_id] = _PENDING_ENTITY_LABEL
            else:
                display[script_id] = str(entity_id)
    return display


async def _bounded_gather(coros: Iterable, *, limit: int = 10):
    """Run awaitables with a concurrency limit and collect results.

    Items in `coros` are callables or coroutine objects; each will be awaited
    under a semaphore to cap concurrent tasks to `limit`.
    """
    sem = asyncio.Semaphore(max(1, int(limit)))

    async def _run(coro_or_fn):
        async with sem:
            if callable(coro_or_fn):
                return await coro_or_fn()
            return await coro_or_fn

    tasks = [asyncio.create_task(_run(c)) for c in coros]
    return await asyncio.gather(*tasks)


async def _build_script_tick_map(
    current_simulation_id: str, current_tick: Optional[int], user_scripts: Iterable
) -> Dict[str, Optional[int]]:
    result: Dict[str, Optional[int]] = {}
    if current_simulation_id and current_tick is not None:
        result[current_simulation_id] = current_tick

    simulation_ids = {
        script.simulation_id
        for script in user_scripts
        if getattr(script, "simulation_id", None)
        and script.simulation_id != current_simulation_id
    }

    async def _fetch_tick(sid: str):
        try:
            state = await _orchestrator.get_state(sid)
        except SimulationNotFoundError:
            return (sid, None)
        else:
            return (sid, getattr(state, "tick", None))

    if simulation_ids:
        pairs = await _bounded_gather([_fetch_tick(sid) for sid in simulation_ids])
        for sid, tick in pairs:
            result[sid] = tick  # may be None

    return result


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    if _get_session_user(request):
        return RedirectResponse(url="/web/dashboard", status_code=303)
    return RedirectResponse(url="/web/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, message: Optional[str] = None) -> HTMLResponse:
    return _templates.TemplateResponse(
        request,
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
            request,
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
        "display_name": getattr(profile, "display_name", None),
        "avatar_url": getattr(profile, "avatar_url", None),
    }
    return RedirectResponse(url="/web/dashboard", status_code=303)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.pop("user", None)
    return RedirectResponse(url="/web/login", status_code=303)


def _normalize_email_for_filename(email: str) -> str:
    safe = email.strip().lower().replace("@", "_at_").replace("/", "_")
    for ch in ["\\", ":", "*", "?", '"', "<", ">", "|"]:
        safe = safe.replace(ch, "_")
    return safe


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request, message: Optional[str] = None, error: Optional[str] = None
) -> HTMLResponse:
    user = await _require_session_user(request)
    profile = await user_manager.get_profile(user["email"])  # type: ignore[index]
    if profile is None:
        return RedirectResponse(url="/web/login", status_code=303)

    # Notifications MVP: 聚合该用户在其脚本所在仿真中的失败记录
    recent_failures: List[Dict[str, Any]] = []
    try:
        scripts = await script_registry.list_user_scripts(profile.email)
        sim_ids = sorted({s.simulation_id for s in scripts if s.simulation_id})

        async def _fetch_failures(sid: str):
            try:
                return await _orchestrator.list_recent_script_failures(sid, limit=50)
            except Exception:
                return []

        if sim_ids:
            batches = await _bounded_gather([_fetch_failures(sid) for sid in sim_ids])
            for events in batches:
                for ev in events:
                    if getattr(ev, "user_id", "").lower() == profile.email.lower():
                        recent_failures.append(
                            {
                                "simulation_id": ev.simulation_id,
                                "agent_kind": ev.agent_kind.value,
                                "entity_id": ev.entity_id,
                                "message": ev.message,
                                "occurred_at": ev.occurred_at,
                            }
                        )
            recent_failures.sort(key=lambda x: x.get("occurred_at"), reverse=True)
    except Exception:
        recent_failures = []

    return _templates.TemplateResponse(
        request,
        "profile.html",
        {
            "request": request,
            "user": {"email": profile.email, "user_type": profile.user_type},
            "profile": profile,
            "message": message,
            "error": error,
            "recent_failures": recent_failures,
        },
    )


@router.post("/profile/display_name")
async def update_display_name(
    request: Request,
    display_name: str = Form(""),
) -> RedirectResponse:
    user = await _require_session_user(request)
    try:
        await user_manager.update_display_name(user["email"], display_name)
    except Exception as exc:
        return _redirect_to_profile(error=f"更新昵称失败：{exc}")
    return _redirect_to_profile(message="昵称已更新。")


@router.post("/profile/password")
async def change_password(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
) -> RedirectResponse:
    if (new_password or "") != (confirm_password or ""):
        return _redirect_to_profile(error="两次输入的新密码不一致。")
    user = await _require_session_user(request)
    try:
        await user_manager.change_password(user["email"], old_password, new_password)
    except AuthenticationError as exc:
        return _redirect_to_profile(error=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        return _redirect_to_profile(error=f"修改密码失败：{exc}")
    return _redirect_to_profile(message="密码已更新。")


@router.post("/profile/email")
async def change_email(
    request: Request,
    new_email: str = Form(...),
    current_password: str = Form(...),
) -> RedirectResponse:
    user = await _require_session_user(request)
    try:
        updated = await user_manager.change_email(
            user["email"], new_email, current_password
        )
    except (AuthenticationError, ValueError) as exc:
        return _redirect_to_profile(error=str(exc))
    # Update session
    request.session["user"]["email"] = updated.email
    return _redirect_to_profile(message="邮箱已更新。")


@router.post("/profile/avatar")
async def update_avatar(
    request: Request,
    avatar_file: Optional[UploadFile] = File(None),
    avatar_url: str = Form(""),
) -> RedirectResponse:
    user = await _require_session_user(request)
    email = user["email"]
    try:
        url_value: Optional[str] = None
        if avatar_file and avatar_file.filename:
            # Save uploaded file to static/avatars
            _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            filename_base = _normalize_email_for_filename(email)
            mime = (
                avatar_file.content_type
                or mimetypes.guess_type(avatar_file.filename)[0]
            )
            ext = ".png"
            if mime and "/" in mime:
                subtype = mime.split("/", 1)[1]
                if subtype in {"jpeg", "jpg"}:
                    ext = ".jpg"
                elif subtype in {"png"}:
                    ext = ".png"
                elif subtype in {"gif"}:
                    ext = ".gif"
            target_path = _AVATAR_DIR / f"{filename_base}{ext}"
            content = await avatar_file.read()
            # rudimentary size guard: 2MB
            if len(content) > 2 * 1024 * 1024:
                return _redirect_to_profile(error="头像文件过大（最大 2MB）。")
            target_path.write_bytes(content)
            url_value = f"/web/static/avatars/{target_path.name}"
        elif avatar_url.strip():
            url_value = avatar_url.strip()
        await user_manager.update_avatar_url(email, url_value)
        # keep session in sync for top-bar avatar
        if "user" in request.session:
            request.session["user"]["avatar_url"] = url_value
    except Exception as exc:
        return _redirect_to_profile(error=f"更新头像失败：{exc}")
    return _redirect_to_profile(message="头像已更新。")


def _redirect_to_profile(
    message: Optional[str] = None, error: Optional[str] = None
) -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"/web/profile{query}", status_code=303)


def _extract_view_data(
    world_state: Dict[str, Any],
    user_type: str,
    user_email: str = "",
) -> Dict[str, Any]:
    world = _as_dict(world_state)
    macro = _as_dict(world.get("macro"))
    firm = _as_dict(world.get("firm"))
    bank = _as_dict(world.get("bank"))
    government = _as_dict(world.get("government"))
    central_bank = _as_dict(world.get("central_bank"))

    def _macro_rows() -> List[tuple[Any, ...]]:
        return [
            _table_row("当前 Tick", world.get("tick"), is_int=True),
            _table_row("当前仿真日", world.get("day"), is_int=True),
            _table_row("GDP", macro.get("gdp")),
            _table_row("通胀率", macro.get("inflation")),
            _table_row("失业率", macro.get("unemployment_rate")),
            _table_row("物价指数", macro.get("price_index")),
            _table_row("工资指数", macro.get("wage_index")),
        ]

    if user_type == "individual":
        households_summary = _summarize_households_data(world.get("households"))
        employment_rate = households_summary.get("employment_rate")
        employment_display: Optional[str]
        if isinstance(employment_rate, (int, float)):
            employment_display = f"{employment_rate * 100:.1f}%"
        else:
            employment_display = None

        market_rows = [
            _table_row("商品价格", firm.get("price")),
            _table_row("企业工资报价", firm.get("wage_offer")),
            _table_row("政府岗位工资", government.get("wage_offer")),
            _table_row("存款利率", bank.get("deposit_rate")),
            _table_row("贷款利率", bank.get("loan_rate")),
            _table_row("税率", government.get("tax_rate")),
        ]

        return {
            "role": "individual",
            "macro_rows": _macro_rows(),
            "market_rows": market_rows,
        }

    if user_type == "firm":
        balance = _as_dict(firm.get("balance_sheet"))
        firm_rows = [
            _table_row("产品价格", firm.get("price")),
            _table_row("计划产出", firm.get("planned_production")),
            _table_row("工资报价", firm.get("wage_offer")),
            _table_row("雇员数量", len(firm.get("employees", []) or []), is_int=True),
            _table_row("最近销售额", firm.get("last_sales")),
            _table_row("现金", balance.get("cash")),
            _table_row("存款", balance.get("deposits")),
            _table_row("贷款", balance.get("loans")),
            _table_row("库存", balance.get("inventory_goods")),
        ]
        labor_rows = [
            _table_row("劳动力市场失业率", macro.get("unemployment_rate")),
            _table_row("政府岗位工资", government.get("wage_offer")),
            _table_row(
                "家庭平均工资收入",
                _summarize_households_data(world.get("households")).get(
                    "avg_wage_income"
                ),
            ),
        ]
        finance_rows = [
            _table_row("存款利率", bank.get("deposit_rate")),
            _table_row("贷款利率", bank.get("loan_rate")),
            _table_row("政策基准利率", central_bank.get("base_rate")),
        ]
        return {
            "role": "firm",
            "macro_rows": _macro_rows(),
            "agent_rows": firm_rows,
            "labor_rows": labor_rows,
            "finance_rows": finance_rows,
        }

    if user_type == "government":
        balance = _as_dict(government.get("balance_sheet"))
        fiscal_rows = [
            _table_row("税率", government.get("tax_rate")),
            _table_row("失业补贴", government.get("unemployment_benefit")),
            _table_row("财政支出", government.get("spending")),
            _table_row(
                "公共岗位数量", len(government.get("employees", []) or []), is_int=True
            ),
            _table_row("现金", balance.get("cash")),
            _table_row("存款", balance.get("deposits")),
            _table_row("贷款", balance.get("loans")),
        ]
        labor_rows = [
            _table_row("失业率", macro.get("unemployment_rate")),
            _table_row("企业工资报价", firm.get("wage_offer")),
            _table_row("政府岗位工资", government.get("wage_offer")),
        ]
        finance_rows = [
            _table_row("政策基准利率", central_bank.get("base_rate")),
            _table_row("准备金率", central_bank.get("reserve_ratio")),
            _table_row("贷款利率", bank.get("loan_rate")),
        ]
        return {
            "role": "government",
            "macro_rows": _macro_rows(),
            "fiscal_rows": fiscal_rows,
            "labor_rows": labor_rows,
            "finance_rows": finance_rows,
        }

    if user_type == "commercial_bank":
        balance = _as_dict(bank.get("balance_sheet"))
        bank_rows = [
            _table_row("存款", balance.get("deposits")),
            _table_row("现金", balance.get("cash")),
            _table_row("贷款", balance.get("loans")),
            _table_row("库存资产", balance.get("inventory_goods")),
        ]
        rate_rows = [
            _table_row("存款利率", bank.get("deposit_rate")),
            _table_row("贷款利率", bank.get("loan_rate")),
            _table_row("政策基准利率", central_bank.get("base_rate")),
            _table_row("法定准备金率", central_bank.get("reserve_ratio")),
        ]
        return {
            "role": "commercial_bank",
            "macro_rows": _macro_rows(),
            "bank_rows": bank_rows,
            "policy_rows": rate_rows,
        }

    if user_type == "central_bank":
        policy_rows = [
            _table_row("基准利率", central_bank.get("base_rate")),
            _table_row("准备金率", central_bank.get("reserve_ratio")),
            _table_row("通胀目标", central_bank.get("inflation_target")),
            _table_row("失业目标", central_bank.get("unemployment_target")),
        ]
        banking_rows = [
            _table_row("银行存款利率", bank.get("deposit_rate")),
            _table_row("银行贷款利率", bank.get("loan_rate")),
            _table_row(
                "银行贷款规模", _as_dict(bank.get("balance_sheet")).get("loans")
            ),
            _table_row("银行现金", _as_dict(bank.get("balance_sheet")).get("cash")),
        ]
        return {
            "role": "central_bank",
            "macro_rows": _macro_rows(),
            "policy_rows": policy_rows,
            "banking_rows": banking_rows,
        }

    return {"role": user_type, "world": world}


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Dict[str, Any] = Depends(_require_session_user),
    simulation_id: str = "",
    tab: Optional[str] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> HTMLResponse:
    allow_create = user["user_type"] == "admin"
    all_simulations = await _orchestrator.list_simulations()
    user_scripts: List = []
    attachable_scripts: List = []
    features_by_sim: Dict[str, Optional[Dict[str, Any]]] = {}
    household_counts_by_sim: Dict[str, int] = {}
    ticks_per_day_default = _get_ticks_per_day_default()
    remaining_ticks_by_sim: Dict[str, Optional[int]] = {}
    user_profiles: List[Any] = []
    user_type_index: Dict[str, str] = {}
    scripts_by_user: Dict[str, List] = {}
    scripts_by_sim: Dict[str, List] = {}
    all_scripts: List = []
    script_failures: List = []
    base_form_defaults = _default_script_form_defaults(user["user_type"])
    if allow_create:
        user_profiles = await user_manager.list_users()
        user_type_index = {
            profile.email.lower(): profile.user_type for profile in user_profiles
        }
        all_scripts = await script_registry.list_all_scripts()
        for metadata in all_scripts:
            scripts_by_user.setdefault(metadata.user_id, []).append(metadata)
            sim_id = metadata.simulation_id
            if sim_id:
                bucket = scripts_by_sim.setdefault(sim_id, [])
                bucket.append(metadata)

        # Compute household counts from live registry per simulation to avoid
        # relying on potentially stale in-memory mappings built earlier.
        async def _count_household_owners(sid: str) -> tuple[str, int]:
            try:
                scripts = await script_registry.list_scripts(sid)
            except Exception:
                return (sid, 0)
            owners = {
                meta.user_id.lower()
                for meta in scripts
                if meta.agent_kind is not None and meta.agent_kind.value == "household"
            }
            return (sid, len(owners))

        if all_simulations:
            pairs = await _bounded_gather(
                [_count_household_owners(s) for s in all_simulations]
            )
            for sid, count in pairs:
                household_counts_by_sim[sid] = count

        # compute remaining to day-end for admins (parallel)
        async def _fetch_state(sid: str):
            try:
                return (sid, await _orchestrator.get_state(sid))
            except SimulationNotFoundError:
                return (sid, None)

        if all_simulations:
            pairs = await _bounded_gather([_fetch_state(s) for s in all_simulations])
            for sid, st in pairs:
                if not st or ticks_per_day_default <= 0:
                    remaining_ticks_by_sim[sid] = None
                    continue
                tick_val = _extract_tick_from_state(st)
                if isinstance(tick_val, int):
                    mod = tick_val % ticks_per_day_default
                    remaining_ticks_by_sim[sid] = (
                        0 if mod == 0 else (ticks_per_day_default - mod)
                    )
                else:
                    remaining_ticks_by_sim[sid] = None

    if not allow_create:
        user_scripts = await script_registry.list_user_scripts(user["email"])
        attachable_scripts = [
            script for script in user_scripts if not script.simulation_id
        ]
    elif all_simulations:

        async def _fetch_features(sid: str):
            try:
                fm = await _orchestrator.get_simulation_features(sid)
                return (sid, fm.model_dump(mode="json"))
            except SimulationNotFoundError:
                return (sid, None)

        pairs = await _bounded_gather([_fetch_features(s) for s in all_simulations])
        for sid, data in pairs:
            features_by_sim[sid] = data

    normalized_id = (simulation_id or "").strip()
    if not normalized_id:
        if all_simulations:
            normalized_id = all_simulations[0]
        else:
            normalized_id = ""
    simulation_id = normalized_id

    default_script_limit = script_registry.get_default_limit()
    limits_by_sim: Dict[str, Optional[int]] = {}
    script_limit: Optional[int] = None

    if allow_create:
        for sid in all_simulations:
            limits_by_sim[sid] = await script_registry.get_simulation_limit(sid)

    if simulation_id:
        if allow_create:
            script_limit = limits_by_sim.get(simulation_id)
            if simulation_id not in limits_by_sim:
                script_limit = await script_registry.get_simulation_limit(simulation_id)
        else:
            script_limit = await script_registry.get_simulation_limit(simulation_id)

    current_tick: Optional[int] = None

    # normalize active tab
    raw_tab = (tab or "").strip().lower()
    active_tab = raw_tab or ("overview")
    show_all = not bool(raw_tab)

    if not allow_create and not simulation_id:
        script_tick_map = await _build_script_tick_map("", None, user_scripts)
        friendly_message = (
            message or "当前没有可加入的仿真世界，请稍后再试或联系管理员创建新实例。"
        )
        return _templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "active_tab": active_tab,
                "show_all": show_all,
                "scripts": [],
                "context": {},
                "error": None,
                "message": friendly_message,
                "all_simulations": all_simulations,
                "all_users": [],
                "all_scripts": [],
                "scripts_by_user": {},
                "user_scripts": user_scripts,
                "attachable_scripts": attachable_scripts,
                "log_download_url": None,
                "script_limit": script_limit,
                "default_script_limit": default_script_limit,
                "limits_by_sim": limits_by_sim,
                "features": None,
                "features_by_sim": features_by_sim,
                "current_simulation_tick": None,
                "script_tick_map": script_tick_map,
                "household_counts_by_sim": household_counts_by_sim,
                "script_failures": script_failures,
                "entity_display_map": _build_entity_display_map(user_scripts),
                "script_form_defaults": base_form_defaults,
                "ticks_per_day_default": ticks_per_day_default,
                "remaining_ticks_by_sim": remaining_ticks_by_sim,
            },
            status_code=200,
        )

    if allow_create and not simulation_id:
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
            request,
            "admin_dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "active_tab": active_tab,
                "show_all": show_all,
                "scripts": [],
                "context": {"world": {}},
                "error": error,
                "message": message,
                "all_simulations": all_simulations,
                "all_users": all_users,
                "all_scripts": all_scripts,
                "scripts_by_user": scripts_by_user,
                "user_scripts": [],
                "attachable_scripts": [],
                "log_download_url": None,
                "script_limit": script_limit,
                "default_script_limit": default_script_limit,
                "limits_by_sim": limits_by_sim,
                "features": None,
                "features_by_sim": features_by_sim,
                "current_simulation_tick": None,
                "script_tick_map": {},
                "household_counts_by_sim": household_counts_by_sim,
                "script_failures": script_failures,
                "ticks_per_day_default": ticks_per_day_default,
                "remaining_ticks_by_sim": remaining_ticks_by_sim,
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
        scripts_for_view: List = []
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
        if resolved_simulation_id:
            scripts_for_view = await script_registry.list_scripts(
                resolved_simulation_id
            )
        simulation_id = resolved_simulation_id
        script_tick_map = await _build_script_tick_map(
            simulation_id, None, user_scripts
        )
        return _templates.TemplateResponse(
            request,
            template_name,
            {
                "request": request,
                "user": user,
                "simulation_id": simulation_id,
                "active_tab": active_tab,
                "show_all": show_all,
                "scripts": scripts_for_view,
                "context": context,
                "error": friendly_error,
                "message": friendly_message,
                "all_simulations": all_simulations,
                "all_users": [],
                "all_scripts": [],
                "scripts_by_user": {},
                "user_scripts": user_scripts,
                "attachable_scripts": attachable_scripts,
                "log_download_url": (
                    f"/web/logs/{simulation_id}/download" if simulation_id else None
                ),
                "script_limit": script_limit,
                "default_script_limit": default_script_limit,
                "limits_by_sim": limits_by_sim,
                "features": None,
                "features_by_sim": features_by_sim,
                "current_simulation_tick": None,
                "script_tick_map": script_tick_map,
                "household_counts_by_sim": household_counts_by_sim,
                "script_failures": script_failures,
                "entity_display_map": _build_entity_display_map(
                    scripts_for_view, user_scripts, attachable_scripts
                ),
                "script_form_defaults": base_form_defaults,
                "ticks_per_day_default": ticks_per_day_default,
                "remaining_ticks_by_sim": remaining_ticks_by_sim,
            },
            status_code=404,
        )

    scripts: List = []
    if simulation_id:
        scripts = await script_registry.list_scripts(simulation_id)
    role_state = _extract_view_data(
        world_state,
        user["user_type"],
        user.get("email", ""),
    )
    context: Dict[str, Any] = {"role_state": role_state}
    template_name = "dashboard.html"
    features_for_view = (
        world_state.get("features") if isinstance(world_state, dict) else None
    )
    current_tick = world_state.get("tick") if isinstance(world_state, dict) else None
    prepared_world = _prepare_world_for_template(world_state)
    if allow_create and simulation_id:
        features_by_sim[simulation_id] = features_for_view
    all_users: List[Dict[str, Any]] = []

    # For non-admin users, collect any household entities owned by this user
    # within the current simulation so we can show per-user household info.
    #
    # Strategy (in order):
    # 1. Query authoritative per-simulation scripts and filter by user.
    # 2. Fall back to the user's script list if needed.
    # 3. If still nothing, fall back to participants and finally try to
    #    match households by any owner field present in the prepared world.
    owned_households: List[tuple] = []
    try:
        if user.get("user_type") != "admin" and simulation_id:
            households_map = prepared_world.get("households", {}) or {}

            def _lookup_household(eid: object) -> Optional[dict]:
                # Try direct key, string key, int key, then scan str-equality
                if eid is None:
                    return None
                # direct
                if eid in households_map:
                    return households_map[eid]
                s = str(eid)
                if s in households_map:
                    return households_map[s]
                try:
                    i = int(eid)
                except Exception:
                    i = None
                if i is not None and i in households_map:
                    return households_map[i]
                # final scan: compare stringified keys
                for k, v in households_map.items():
                    try:
                        if str(k) == s:
                            return v
                    except Exception:
                        continue
                return None

            # 1) authoritative per-simulation scripts
            try:
                sim_scripts = await script_registry.list_scripts(simulation_id)
            except Exception:
                sim_scripts = []

            user_email = user.get("email", "")
            matched = []
            for meta in sim_scripts:
                if (
                    getattr(meta, "user_id", None) == user_email
                    and getattr(meta, "agent_kind", None) == AgentKind.HOUSEHOLD
                ):
                    eid = getattr(meta, "entity_id", None)
                    hh = _lookup_household(eid)
                    if hh:
                        matched.append((eid, hh))

            # 2) fallback: user's scripts (may include scripts across sims)
            if not matched:
                try:
                    my_scripts = (
                        user_scripts
                        if user_scripts is not None
                        else await script_registry.list_user_scripts(user_email)
                    )
                except Exception:
                    my_scripts = []
                for meta in my_scripts:
                    if (
                        getattr(meta, "simulation_id", None) == simulation_id
                        and getattr(meta, "agent_kind", None) == AgentKind.HOUSEHOLD
                    ):
                        eid = getattr(meta, "entity_id", None)
                        hh = _lookup_household(eid)
                        if hh:
                            matched.append((eid, hh))

            # 3) fallback: participants + owner-field scan
            if not matched:
                try:
                    participants = await _orchestrator.list_participants(simulation_id)
                except Exception:
                    participants = []
                if user_email in participants:
                    # try to find households that assert an owner field
                    for k, v in households_map.items():
                        owner = (
                            v.get("owner") or v.get("owner_email") or v.get("user_id")
                        )
                        try:
                            if owner and owner == user_email:
                                matched.append((k, v))
                        except Exception:
                            continue

            owned_households = matched
    except Exception:
        # on any unexpected error, fall back to empty list (don't raise)
        owned_households = []

    if allow_create:
        template_name = "admin_dashboard.html"
        context["world"] = prepared_world
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
        if simulation_id:
            script_failures = await _orchestrator.list_recent_script_failures(
                simulation_id, limit=50
            )

    script_tick_map = await _build_script_tick_map(
        simulation_id, current_tick, user_scripts
    )

    return _templates.TemplateResponse(
        request,
        template_name,
        {
            "request": request,
            "user": user,
            "simulation_id": simulation_id,
            "active_tab": active_tab,
            "show_all": show_all,
            "scripts": scripts,
            "context": context,
            "error": error,
            "message": message,
            "all_simulations": all_simulations,
            "all_users": all_users,
            "all_scripts": all_scripts,
            "scripts_by_user": scripts_by_user,
            "user_scripts": user_scripts,
            "attachable_scripts": attachable_scripts,
            "log_download_url": (
                f"/web/logs/{simulation_id}/download" if simulation_id else None
            ),
            "script_limit": script_limit,
            "default_script_limit": default_script_limit,
            "limits_by_sim": limits_by_sim,
            "features": features_for_view,
            "features_by_sim": features_by_sim,
            "current_simulation_tick": current_tick,
            "script_tick_map": script_tick_map,
            "household_counts_by_sim": household_counts_by_sim,
            "script_failures": script_failures,
            "entity_display_map": _build_entity_display_map(
                scripts, user_scripts, attachable_scripts
            ),
            "script_form_defaults": base_form_defaults,
            "ticks_per_day_default": ticks_per_day_default,
            "remaining_ticks_by_sim": remaining_ticks_by_sim,
            "owned_households": owned_households,
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
            error="管理员无需切换仿真实例。",
        )

    target = simulation_id.strip()
    if not target:
        return _redirect_to_dashboard(
            "",
            error="请选择仿真实例后再查看。",
        )

    try:
        await _orchestrator.get_state(target)
    except SimulationNotFoundError:
        return _redirect_to_dashboard(
            "",
            error=f"仿真实例 {target} 不存在，请刷新列表后重试。",
        )

    return _redirect_to_dashboard(
        target,
        message=f"已切换至仿真实例 {target} 的视图。",
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


@router.post("/admin/simulations/script_limit")
async def admin_update_script_limit(
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(...),
    max_scripts_per_user: str = Form(""),
    submit_action: str = Form("apply"),
    current_simulation_id: str = Form("default-simulation"),
) -> RedirectResponse:
    target = simulation_id.strip()
    fallback = current_simulation_id.strip() or "default-simulation"

    if not target:
        return _redirect_to_dashboard(
            fallback,
            error="请指定仿真实例 ID。",
        )

    action = (submit_action or "apply").strip().lower()
    raw_value = (max_scripts_per_user or "").strip()
    limit_value: Optional[int]

    try:
        if action == "clear" or not raw_value:
            limit_value = None
        else:
            limit_value = int(raw_value)
        applied = await _orchestrator.set_script_limit(target, limit_value)
    except SimulationStateError as exc:
        redirect_target = target or fallback
        return _redirect_to_dashboard(
            redirect_target,
            error=(
                f"仿真实例 {target or fallback} 已运行到 tick {exc.tick}，"
                "无法再调整脚本数量上限。"
            ),
        )
    except ValueError as exc:
        detail = str(exc)
        redirect_target = target or fallback
        if "script limit must be positive" in detail:
            friendly_error = "脚本上限必须为正整数，或留空表示不设限制。"
        elif detail.startswith("Existing scripts exceed"):
            friendly_error = (
                "部分用户已拥有超过该上限的脚本，请先移除多余脚本后再尝试。"
            )
        else:
            friendly_error = "脚本上限必须为正整数，或留空表示不设限制。"
        return _redirect_to_dashboard(redirect_target, error=friendly_error)
    except SimulationNotFoundError:
        redirect_target = target or fallback
        return _redirect_to_dashboard(
            redirect_target,
            error=f"仿真实例 {target or fallback} 不存在，无法更新脚本上限。",
        )

    if applied is None:
        note = f"已取消仿真实例 {target} 的脚本上限限制。"
    else:
        note = f"仿真实例 {target} 的脚本上限已更新为每位用户 {applied} 个脚本。"

    return _redirect_to_dashboard(target, message=note)


@router.post("/admin/simulations/features")
async def admin_update_features(
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(...),
    household_shock_enabled: Optional[str] = Form(None),
    household_shock_ability_std: str = Form(""),
    household_shock_asset_std: str = Form(""),
    household_shock_max_fraction: str = Form(""),
    current_simulation_id: str = Form("default-simulation"),
) -> RedirectResponse:
    target = simulation_id.strip()
    fallback = current_simulation_id.strip() or "default-simulation"

    if not target:
        return _redirect_to_dashboard(
            fallback,
            error="请指定仿真实例 ID。",
        )

    enabled = household_shock_enabled == "1"

    def _parse_float(raw: str, field_label: str) -> Optional[float]:
        value = raw.strip()
        if not value:
            return None
        try:
            parsed = float(value)
        except ValueError:
            raise ValueError(f"{field_label} 必须是数值。")
        return parsed

    try:
        updates: Dict[str, object] = {"household_shock_enabled": enabled}
        ability_std = _parse_float(household_shock_ability_std, "能力冲击标准差")
        asset_std = _parse_float(household_shock_asset_std, "资产冲击强度")
        max_fraction = _parse_float(household_shock_max_fraction, "冲击上限占比")

        if ability_std is not None and ability_std < 0.0:
            raise ValueError("能力冲击标准差必须大于等于 0。")
        if asset_std is not None and asset_std < 0.0:
            raise ValueError("资产冲击强度必须大于等于 0。")
        if max_fraction is not None and not (0.0 <= max_fraction <= 0.9):
            raise ValueError("冲击上限占比需位于 0 到 0.9 之间。")

        if ability_std is not None:
            updates["household_shock_ability_std"] = ability_std
        if asset_std is not None:
            updates["household_shock_asset_std"] = asset_std
        if max_fraction is not None:
            updates["household_shock_max_fraction"] = max_fraction

        await _orchestrator.update_simulation_features(target, **updates)
    except ValueError as exc:
        return _redirect_to_dashboard(target, error=str(exc))
    except SimulationStateError as exc:
        return _redirect_to_dashboard(
            fallback,
            error=(
                f"仿真实例 {target} 已运行到 tick {exc.tick}，"
                "无法再调整外生冲击配置。"
            ),
        )
    except SimulationNotFoundError:
        return _redirect_to_dashboard(
            fallback,
            error=f"仿真实例 {target} 不存在，无法更新功能开关。",
        )

    state = await _orchestrator.get_state(target)
    status_label = "已启用" if state.features.household_shock_enabled else "已关闭"
    note = (
        f"仿真实例 {target} 的家户异质性冲击功能 {status_label}，" f"参数已同步更新。"
    )
    return _redirect_to_dashboard(target, message=note)


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


@router.post("/admin/simulations/run_one_day")
async def admin_run_one_day(
    request: Request,
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(...),
    ticks_per_day: str = Form(""),
    current_simulation_id: str = Form("default-simulation"),
):
    target = simulation_id.strip() or current_simulation_id or "default-simulation"
    custom_ticks: Optional[int] = None
    raw = (ticks_per_day or "").strip()
    if raw:
        try:
            custom_ticks = int(raw)
        except (TypeError, ValueError):
            return _async_response(
                request, target, error="请输入合法的 Tick 数（正整数）。"
            )
        if custom_ticks <= 0:
            return _async_response(request, target, error="Tick 数必须大于 0。")

    try:
        result = await _orchestrator.run_day(
            target,
            ticks_per_day=custom_ticks,
        )
    except SimulationNotFoundError:
        return _async_response(
            request,
            current_simulation_id or target,
            error=f"仿真实例 {target} 不存在，无法执行日批处理。",
        )
    except ValueError as exc:
        return _async_response(request, target, error=str(exc))

    note = _format_tick_progress_message(
        target,
        tick=result.world_state.tick,
        day=result.world_state.day,
        ticks_executed=result.ticks_executed,
    )
    extra = {
        "tick": result.world_state.tick,
        "day": result.world_state.day,
        "ticks_executed": result.ticks_executed,
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


@router.get("/admin/sandbox_metrics")
async def admin_sandbox_metrics(
    user: Dict[str, Any] = Depends(_require_admin_user),
):
    try:
        from ..script_engine.sandbox import get_sandbox_metrics

        metrics = get_sandbox_metrics()
    except Exception as exc:
        logger.exception("Failed to read sandbox metrics: %s", exc)
        raise HTTPException(status_code=500, detail="无法获取沙箱指标")
    return JSONResponse(metrics)


@router.post("/admin/smoke_test")
async def admin_smoke_test(
    request: Request,
    user: Dict[str, Any] = Depends(_require_admin_user),
    simulation_id: str = Form(...),
    ticks: str = Form("1"),
):
    # schedule a light-weight smoke test job that runs a few ticks and returns timing
    target = (simulation_id or "").strip()
    try:
        t = int((ticks or "1").strip())
    except Exception:
        return _async_response(request, target, error="请提供合法的 ticks 数（整数）。")

    async def _job_factory() -> Dict[str, Any]:
        start = asyncio.get_event_loop().time()
        try:
            result = await _orchestrator.run_day(target, ticks_per_day=t)
        except Exception as exc:
            raise
        elapsed = asyncio.get_event_loop().time() - start
        return {
            "message": f"Smoke test completed: ran {result.ticks_executed} ticks",
            "extra": {"elapsed_sec": elapsed, "ticks_executed": result.ticks_executed},
        }

    try:
        job = await _background_jobs.enqueue(target, "smoke_test", _job_factory)
    except JobConflictError as exc:
        existing = await _background_jobs.get(exc.existing_job_id)
        extra = {}
        if existing:
            extra = {"job_id": existing.job_id, "job_status": existing.status}
        return _async_response(
            request, target, error="已有 smoke test 在运行", extra=extra
        )

    return _async_response(
        request,
        target,
        message=(f"已启动 smoke test (Job: {job.job_id[:8]}…)"),
        extra={"job_id": job.job_id},
    )


@router.get("/performance", response_class=HTMLResponse)
async def performance_page(
    request: Request, user: Dict[str, Any] = Depends(_require_session_user)
) -> HTMLResponse:
    return _templates.TemplateResponse(
        request, "performance.html", {"request": request, "user": user}
    )


@router.get("/performance/metrics")
async def performance_metrics(user: Dict[str, Any] = Depends(_require_session_user)):
    try:
        from ..script_engine.sandbox import get_sandbox_metrics

        metrics = get_sandbox_metrics()
    except Exception as exc:
        logger.exception("Failed to read sandbox metrics: %s", exc)
        raise HTTPException(status_code=500, detail="无法获取沙箱指标")
    return JSONResponse(metrics)


@router.get("/metrics")
async def prometheus_metrics():
    try:
        from prometheus_client import generate_latest

        metrics = generate_latest()
        # generate_latest returns bytes
        return PlainTextResponse(
            content=metrics.decode("utf-8"),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    except Exception:
        return PlainTextResponse(
            content="# prometheus metrics not available\n", media_type="text/plain"
        )


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
    try:
        if target:
            await _orchestrator.remove_script_from_simulation(target, script_id)
            note = f"已删除仿真实例 {target} 下的脚本 {script_id}。"
        else:
            await script_registry.delete_script_by_id(script_id)
            note = f"脚本 {script_id} 已从系统中移除。"
    except SimulationStateError as exc:
        return _redirect_to_dashboard(
            redirect_target,
            error=(
                f"仿真实例 {target} 已运行到 tick {exc.tick}，"
                "仅在 tick 0 时允许删除挂载的脚本。"
            ),
        )
    except ScriptExecutionError as exc:
        return _redirect_to_dashboard(redirect_target, error=str(exc))

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

    removed = await script_registry.remove_scripts_by_user(normalized)
    note = f"用户 {normalized} 已删除。"
    if removed:
        note += f" 同时移除 {removed} 个脚本。"
    return _redirect_to_dashboard(redirect_target, message=note)


@router.post("/scripts", response_class=HTMLResponse)
async def upload_script(
    request: Request,
    user: Dict[str, Any] = Depends(_require_session_user),
    current_simulation_id: str = Form(""),
    description: str = Form(""),
    agent_kind: Optional[str] = Form(None),
    entity_id: Optional[str] = Form(None),
    script_file: UploadFile = File(...),
) -> HTMLResponse:
    normalized_sim_id = (current_simulation_id or "").strip()
    description_text = (description or "").strip()
    agent_kind_value = (agent_kind or "").strip().lower()
    form_defaults: Dict[str, str] = _default_script_form_defaults(
        user["user_type"], description=description_text
    )

    if user["user_type"] == "admin":
        scripts_for_view: List = []
        if normalized_sim_id:
            scripts_for_view = await script_registry.list_scripts(normalized_sim_id)
        return _templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": normalized_sim_id,
                "scripts": scripts_for_view,
                "context": {},
                "error": "管理员账号不能上传脚本。",
                "script_form_defaults": dict(form_defaults),
                "entity_display_map": _build_entity_display_map(scripts_for_view),
            },
            status_code=403,
        )

    async def render_dashboard_error(
        message: str,
        *,
        status_code: int = 400,
    ) -> HTMLResponse:
        all_simulations = await _orchestrator.list_simulations()
        user_scripts = await script_registry.list_user_scripts(user["email"])
        attachable_scripts = [
            script for script in user_scripts if not script.simulation_id
        ]

        scripts_list: List = []
        context_payload: Dict[str, Any] = {}
        features: Optional[Dict[str, Any]] = None
        log_download_url: Optional[str] = None
        script_limit: Optional[int] = None
        default_script_limit = script_registry.get_default_limit()
        limits_by_sim: Dict[str, Optional[int]] = {}
        features_by_sim: Dict[str, Optional[Dict[str, Any]]] = {}
        current_tick: Optional[int] = None

        if normalized_sim_id:
            scripts_list = await script_registry.list_scripts(normalized_sim_id)
            try:
                world_state_model = await _orchestrator.get_state(normalized_sim_id)
            except SimulationNotFoundError:
                context_payload = {}
            else:
                world_state = world_state_model.model_dump(mode="json")
                context_payload = _extract_view_data(
                    world_state,
                    user["user_type"],
                    user.get("email", ""),
                )
                features = world_state.get("features")
                current_tick = world_state.get("tick")
            script_limit = await script_registry.get_simulation_limit(normalized_sim_id)
            log_download_url = f"/web/logs/{normalized_sim_id}/download"

        script_tick_map = await _build_script_tick_map(
            normalized_sim_id, current_tick, user_scripts
        )

        return _templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "simulation_id": normalized_sim_id,
                "scripts": scripts_list,
                "context": context_payload,
                "error": message,
                "message": None,
                "all_simulations": all_simulations,
                "all_users": [],
                "all_scripts": [],
                "scripts_by_user": {},
                "user_scripts": user_scripts,
                "attachable_scripts": attachable_scripts,
                "log_download_url": log_download_url,
                "script_limit": script_limit,
                "default_script_limit": default_script_limit,
                "limits_by_sim": limits_by_sim,
                "features": features,
                "features_by_sim": features_by_sim,
                "current_simulation_tick": current_tick,
                "script_tick_map": script_tick_map,
                "script_form_defaults": dict(form_defaults),
                "entity_display_map": _build_entity_display_map(
                    scripts_list, user_scripts, attachable_scripts
                ),
            },
            status_code=status_code,
        )

    filename = (script_file.filename or "").strip()
    if not filename:
        await script_file.close()
        return await render_dashboard_error("请选择一个 .py 脚本文件后再提交。")
    if not filename.lower().endswith(".py"):
        await script_file.close()
        return await render_dashboard_error("仅支持上传 .py 文件，请检查文件扩展名。")

    try:
        raw_bytes = await script_file.read()
    except Exception:
        await script_file.close()
        return await render_dashboard_error(
            "上传脚本文件失败，请稍后重试。", status_code=500
        )
    await script_file.close()

    if not raw_bytes:
        return await render_dashboard_error("上传的脚本文件为空，请确认内容后重试。")

    try:
        code = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return await render_dashboard_error(
            "脚本文件必须使用 UTF-8 编码，请重新保存后上传。"
        )

    if not code.strip():
        return await render_dashboard_error(
            "脚本文件内容为空，请填写有效的 generate_decisions 实现。"
        )

    target_simulation = normalized_sim_id

    requested_agent_kind: Optional[AgentKind] = None
    if agent_kind_value:
        try:
            requested_agent_kind = AgentKind(agent_kind_value)
        except ValueError:
            return await render_dashboard_error("请选择有效的目标主体类型。")

    try:
        agent_kind_enum = resolve_agent_kind(
            user["user_type"],
            requested_agent_kind,
            allow_override=False,
        )
    except ValueError:
        return await render_dashboard_error(
            "当前账号类型暂不支持上传脚本。", status_code=403
        )

    if agent_kind_enum in {AgentKind.WORLD, AgentKind.MACRO}:
        return await render_dashboard_error("该主体类型暂不支持自定义脚本。")

    form_defaults = _default_script_form_defaults(
        user["user_type"],
        description=description_text,
        resolved_kind=agent_kind_enum,
    )

    try:
        await script_registry.register_script(
            simulation_id=None,
            user_id=user["email"],
            script_code=code,
            description=description_text or None,
            agent_kind=agent_kind_enum,
        )
    except ScriptExecutionError as exc:
        return await render_dashboard_error(str(exc), status_code=400)

    return _redirect_to_dashboard(
        target_simulation,
        message="脚本已上传到个人脚本库，可在下方选择挂载到仿真实例。",
    )


@router.post("/scripts/attach")
async def attach_existing_script(
    user: Dict[str, Any] = Depends(_require_session_user),
    simulation_id: str = Form(...),
    script_id: str = Form(...),
) -> RedirectResponse:
    if user["user_type"] == "admin":
        return _redirect_to_dashboard(
            simulation_id,
            error="管理员账号不能执行脚本挂载操作。",
        )

    target = simulation_id.strip()
    if not target:
        return _redirect_to_dashboard(
            "",
            error="请选择仿真实例后再挂载脚本。",
        )

    try:
        await _orchestrator.attach_script_to_simulation(
            simulation_id=target,
            script_id=script_id,
            user_id=user["email"],
        )
        await _orchestrator.register_participant(target, user["email"])
    except SimulationStateError as exc:
        return _redirect_to_dashboard(
            target,
            error=(
                f"仿真实例 {target} 已运行到 tick {exc.tick}，"
                "仅在 tick 0 时允许挂载脚本。"
            ),
        )
    except SimulationNotFoundError:
        return _redirect_to_dashboard(
            "",
            error=f"仿真实例 {target} 不存在，请刷新列表后重试。",
        )
    except ScriptExecutionError as exc:
        return _redirect_to_dashboard(target, error=str(exc))

    return _redirect_to_dashboard(
        target,
        message=f"脚本 {script_id} 已挂载至仿真实例 {target}。",
    )


@router.post("/scripts/detach")
async def detach_script(
    user: Dict[str, Any] = Depends(_require_session_user),
    script_id: str = Form(...),
    simulation_id: str = Form(...),
    current_simulation_id: str = Form(""),
) -> RedirectResponse:
    if user["user_type"] == "admin":
        return _redirect_to_dashboard(
            current_simulation_id,
            error="管理员账号不能执行脚本管理操作。",
        )

    target_simulation = simulation_id.strip()
    normalized_current = (current_simulation_id or "").strip()
    redirect_target = normalized_current or target_simulation

    if not target_simulation:
        return _redirect_to_dashboard(
            redirect_target,
            error="请指定要取消挂载的仿真实例。",
        )

    try:
        metadata = await script_registry.get_user_script(script_id, user["email"])
    except ScriptExecutionError as exc:
        return _redirect_to_dashboard(redirect_target, error=str(exc))

    if metadata.simulation_id != target_simulation:
        return _redirect_to_dashboard(
            redirect_target,
            error="脚本未挂载到指定的仿真实例。",
        )

    try:
        state = await _orchestrator.get_state(target_simulation)
    except SimulationNotFoundError:
        return _redirect_to_dashboard(
            redirect_target,
            error=f"仿真实例 {target_simulation} 不存在或尚未初始化。",
        )

    if state.tick != 0:
        return _redirect_to_dashboard(
            redirect_target,
            error="仅在仿真实例处于 Tick 0 时才能取消挂载脚本。",
        )

    try:
        await _orchestrator.detach_script_from_simulation(
            target_simulation, script_id, user["email"]
        )
    except ScriptExecutionError as exc:
        return _redirect_to_dashboard(redirect_target, error=str(exc))

    return _redirect_to_dashboard(
        redirect_target,
        message=f"脚本 {script_id} 已从仿真实例 {target_simulation} 取消挂载。",
    )


@router.post("/scripts/rotate")
async def rotate_script_at_day_end(
    user: Dict[str, Any] = Depends(_require_session_user),
    simulation_id: str = Form(...),
    script_id: str = Form(...),
    new_description: str = Form(""),
    script_file: UploadFile = File(...),
):
    if user["user_type"] == "admin":
        return _redirect_to_dashboard(
            simulation_id,
            error="管理员账号请使用后台操作。",
        )

    target = simulation_id.strip()
    if not target:
        return _redirect_to_dashboard("", error="请选择仿真实例后再进行脚本替换。")

    filename = (script_file.filename or "").strip()
    if not filename or not filename.lower().endswith(".py"):
        await script_file.close()
        return _redirect_to_dashboard(target, error="请上传 .py 类型的脚本文件。")

    try:
        raw = await script_file.read()
    finally:
        await script_file.close()
    if not raw:
        return _redirect_to_dashboard(target, error="上传的脚本文件为空。")

    try:
        new_code = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _redirect_to_dashboard(target, error="脚本文件必须为 UTF-8 编码。")

    try:
        updated = await _orchestrator.update_script_code_at_day_end(
            target,
            script_id=script_id,
            user_id=user.get("email"),
            new_code=new_code,
            new_description=(new_description or None),
        )
    except SimulationNotFoundError:
        return _redirect_to_dashboard("", error=f"仿真实例 {target} 不存在。")
    except ScriptExecutionError as exc:
        return _redirect_to_dashboard(target, error=str(exc))
    except Exception as exc:
        detail = str(exc)
        if "not at day boundary" in detail:
            return _redirect_to_dashboard(
                target, error="仅在日终边界可替换脚本，请先执行完当日 Tick。"
            )
        return _redirect_to_dashboard(target, error=f"替换脚本失败: {exc}")

    return _redirect_to_dashboard(
        target,
        message=f"脚本 {updated.script_id} 已更新代码，下一交易日生效。",
    )


@router.post("/scripts/delete")
async def delete_script(
    user: Dict[str, Any] = Depends(_require_session_user),
    script_id: str = Form(...),
    current_simulation_id: str = Form(""),
) -> RedirectResponse:
    if user["user_type"] == "admin":
        return _redirect_to_dashboard(
            current_simulation_id,
            error="管理员账号不能执行脚本管理操作。",
        )

    normalized_current = (current_simulation_id or "").strip()

    try:
        metadata = await script_registry.get_user_script(script_id, user["email"])
    except ScriptExecutionError as exc:
        return _redirect_to_dashboard(normalized_current, error=str(exc))

    target_simulation = metadata.simulation_id or ""
    redirect_target = normalized_current or target_simulation

    allowed = False
    simulation_tick: Optional[int] = None
    if metadata.simulation_id is None:
        allowed = True
    else:
        try:
            state = await _orchestrator.get_state(metadata.simulation_id)
        except SimulationNotFoundError:
            allowed = True
        else:
            simulation_tick = state.tick
            if simulation_tick == 0:
                allowed = True

    if not allowed:
        if simulation_tick is None:
            message_text = "脚本仅可在仿真实例处于 Tick 0 或未挂载时删除。"
        else:
            message_text = "脚本仅可在仿真实例处于 Tick 0 时删除。"
        return _redirect_to_dashboard(
            redirect_target,
            error=message_text,
        )

    try:
        if metadata.simulation_id is not None:
            await _orchestrator.remove_script_from_simulation(
                metadata.simulation_id, script_id
            )
        else:
            await script_registry.delete_user_script(script_id, user["email"])
    except ScriptExecutionError as exc:
        return _redirect_to_dashboard(redirect_target, error=str(exc))

    return _redirect_to_dashboard(
        redirect_target,
        message=f"脚本 {script_id} 已删除。",
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(
        request,
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
            request,
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
            request,
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
            request,
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
            request,
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


@router.get("/docs")
async def docs_page(request: Request):
    """Redirect the docs route to the external GitHub Pages site.

    The project maintains documentation on GitHub Pages at
    https://ceciliaguo331.github.io/econ.simulator.doc/; instead of
    rendering internal markdown, just redirect users there.
    """
    external = "https://ceciliaguo331.github.io/econ.simulator.doc/"
    return RedirectResponse(url=external, status_code=302)


@router.get("/logs/{simulation_id}/download")
async def download_recent_logs(
    simulation_id: str,
    limit: int = 500,
    user: Dict[str, Any] = Depends(_require_session_user),
):
    target = simulation_id.strip()
    if not target:
        raise HTTPException(status_code=400, detail="仿真实例 ID 不能为空。")

    limit = max(1, min(limit, 1000))

    try:
        if user.get("user_type") != "admin":
            participants = await _orchestrator.list_participants(target)
            if user.get("email") not in participants:
                raise HTTPException(
                    status_code=403, detail="只有加入该仿真实例的用户才能下载日志。"
                )
        logs = await _orchestrator.get_recent_logs(target, limit=limit)
    except SimulationNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="仿真实例不存在或尚未初始化。"
        ) from exc

    text = _format_logs_for_download(logs)
    buffer = io.BytesIO(text.encode("utf-8"))
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{target}-logs.txt"'}
    return StreamingResponse(
        buffer,
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )

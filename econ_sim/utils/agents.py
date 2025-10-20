"""
用户类型到代理（AgentKind）映射工具。

该模块提供将系统中表示用户角色的字符串（如 'individual', 'firm' 等）
映射为内部使用的 AgentKind 枚举值的工具函数。主要用于在脚本或 API 层
根据用户身份自动确定其可创建/操作的代理类型。

函数说明：
- `resolve_agent_kind(user_type, requested, allow_override)`：在绝大多数场景下返回
    与 user_type 对应的默认 AgentKind；当允许覆盖（管理员场景）时，可接受显式请求。
- `get_default_agent_kind(user_type)`：便捷函数，返回默认映射或 None。

该模块对输入进行规范化与基本校验，若用户类型无法识别会抛出 ValueError，
调用方应在上层将其转换为合适的 HTTP 400/403 响应。
"""

from __future__ import annotations

from typing import Optional

from ..data_access.models import AgentKind

_USER_TYPE_AGENT_KIND_MAP = {
    "individual": AgentKind.HOUSEHOLD,
    "firm": AgentKind.FIRM,
    "government": AgentKind.GOVERNMENT,
    "commercial_bank": AgentKind.BANK,
    "central_bank": AgentKind.CENTRAL_BANK,
}


def resolve_agent_kind(
    user_type: str,
    requested: Optional[AgentKind] = None,
    *,
    allow_override: bool = False,
) -> AgentKind:
    """Resolve the agent kind for a given user type.

    Parameters
    ----------
    user_type:
        The textual user role stored in the session or user profile.
    requested:
        Optional explicit agent kind requested by the caller. When ``allow_override``
        is False, the requested value must match the default mapping; otherwise a
        ``ValueError`` is raised. Administrators may set ``allow_override`` to
        True to bypass this restriction.
    allow_override:
        Whether to honour the ``requested`` value regardless of the default mapping.

    Returns
    -------
    AgentKind
        The resolved agent kind for the user.
    """

    normalized = (user_type or "").strip().lower()
    mapped = _USER_TYPE_AGENT_KIND_MAP.get(normalized)

    if mapped is None:
        if allow_override and requested is not None:
            return requested
        raise ValueError("Unsupported user type for agent kind resolution")

    if allow_override and requested is not None:
        return requested

    if requested is not None and requested != mapped:
        raise ValueError("Requested agent kind does not match user type")

    return mapped


def get_default_agent_kind(user_type: str) -> Optional[AgentKind]:
    """Return the default agent kind for a user type, or ``None`` if undefined."""

    return _USER_TYPE_AGENT_KIND_MAP.get((user_type or "").strip().lower())


__all__ = [
    "resolve_agent_kind",
    "get_default_agent_kind",
]

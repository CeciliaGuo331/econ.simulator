"""邮箱与用户类型等字段的校验工具。"""

from __future__ import annotations

import re

_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PUBLIC_USER_TYPES = {
    "individual",
    "firm",
    "government",
    "commercial_bank",
    "central_bank",
}
ADMIN_USER_TYPE = "admin"


def validate_email(email: str) -> str:
    """若邮箱格式不合法则抛出 ValueError，合法时返回归一化后的邮箱。"""

    if not _EMAIL_REGEX.match(email.strip()):
        raise ValueError("Invalid email format")
    return email


def validate_user_type(user_type: str, *, allow_admin: bool = False) -> str:
    """校验用户类型是否合法，并返回归一化后的结果。"""

    normalized = user_type.strip().lower()
    allowed = set(PUBLIC_USER_TYPES)
    if allow_admin:
        allowed.add(ADMIN_USER_TYPE)
    if normalized not in allowed:
        raise ValueError("Invalid user type")
    return normalized


__all__ = [
    "validate_email",
    "validate_user_type",
    "PUBLIC_USER_TYPES",
    "ADMIN_USER_TYPE",
]

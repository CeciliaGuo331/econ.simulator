"""邮箱等字段的校验工具。"""

from __future__ import annotations

import re

_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email(email: str) -> str:
    """若邮箱格式不合法则抛出 ValueError，合法时返回归一化后的邮箱。"""

    if not _EMAIL_REGEX.match(email.strip()):
        raise ValueError("Invalid email format")
    return email

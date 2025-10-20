"""PostgreSQL 相关操作的共享辅助函数。

当前仅包含用于安全引用 SQL 标识符的 quote_identifier 实用函数，
以减少字符串插入 SQL 时的注入及语法问题风险。
"""

from __future__ import annotations

import re

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_identifier(identifier: str) -> str:
    """将输入标识符安全地包装为 SQL 标识符字符串。

    该函数用于在动态构建 SQL 语句时确保标识符（例如 schema/table/column 名称）
    满足简单的标识符约束（以字母或下划线开头，仅包含字母、数字或下划线），
    并用双引号返回以避免常见的注入或语法问题。

    参数
    ----------
    identifier:
        要引用的标识符字符串（例如 "public"、"world_state_snapshots"）。

    返回
    -------
    str
        已用双引号包裹的安全标识符，例如 '"my_table"'。

    异常
    -------
    ValueError
        如果提供的标识符不符合简单标识符正则规则，则抛出异常以避免不安全的 SQL 插入。
    """

    if not _IDENTIFIER_PATTERN.match(identifier):
        raise ValueError(f"无效的 SQL 标识符: {identifier}")
    return f'"{identifier}"'


__all__ = ["quote_identifier"]

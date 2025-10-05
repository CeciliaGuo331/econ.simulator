"""提供密码哈希与校验的工具函数。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Tuple

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 390_000
_SALT_BYTES = 16


def _encode(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def _decode(data: str) -> bytes:
    return base64.b64decode(data.encode("utf-8"))


def hash_password(password: str) -> str:
    """对明文密码执行 PBKDF2 哈希，返回带有元数据的字符串。"""

    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS
    )
    return f"{_ALGORITHM}${_ITERATIONS}${_encode(salt)}${_encode(derived)}"


def _parse_hash(password_hash: str) -> Tuple[str, int, bytes, bytes]:
    algorithm, iterations, salt_b64, hash_b64 = password_hash.split("$")
    return algorithm, int(iterations), _decode(salt_b64), _decode(hash_b64)


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码是否与存储的哈希匹配。"""

    algorithm, iterations, salt, expected = _parse_hash(password_hash)
    if algorithm != _ALGORITHM:
        raise ValueError("Unsupported password hashing algorithm")
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(derived, expected)

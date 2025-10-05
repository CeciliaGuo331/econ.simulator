"""用户注册与登录管理逻辑。"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Protocol

from pydantic import BaseModel, field_validator

from .validators import (
    ADMIN_USER_TYPE,
    PUBLIC_USER_TYPES,
    validate_email,
    validate_user_type,
)

from .passwords import hash_password, verify_password


class UserAlreadyExistsError(RuntimeError):
    """当尝试注册已存在邮箱时抛出。"""


class AuthenticationError(RuntimeError):
    """登录失败时的统一异常。"""


DEFAULT_ADMIN_EMAIL = "admin@econ.sim"
DEFAULT_ADMIN_PASSWORD = "ChangeMe123!"


@dataclass
class UserRecord:
    """存储用户的持久化信息。"""

    email: str
    password_hash: str
    created_at: datetime
    user_type: str


class UserStore(Protocol):
    async def get_user(self, email: str) -> Optional[UserRecord]: ...

    async def save_user(self, record: UserRecord) -> None: ...

    async def clear(self) -> None: ...


class InMemoryUserStore:
    """简单的内存用户存储，实现并发安全。"""

    def __init__(self) -> None:
        self._users: Dict[str, UserRecord] = {}
        self._lock = asyncio.Lock()

    async def get_user(self, email: str) -> Optional[UserRecord]:
        async with self._lock:
            return self._users.get(email)

    async def save_user(self, record: UserRecord) -> None:
        async with self._lock:
            self._users[record.email] = record

    async def clear(self) -> None:
        async with self._lock:
            self._users.clear()


class RedisUserStore:
    """使用 Redis 存储用户数据。"""

    def __init__(self, redis, prefix: str = "econ_sim") -> None:
        self._redis = redis
        self._key = f"{prefix}:users"
        self._lock = asyncio.Lock()

    async def get_user(self, email: str) -> Optional[UserRecord]:
        async with self._lock:
            raw = await self._redis.hget(self._key, email)
        if raw is None:
            return None
        payload = json.loads(raw)
        return UserRecord(
            email=payload["email"],
            password_hash=payload["password_hash"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            user_type=validate_user_type(
                payload.get("user_type", "individual"), allow_admin=True
            ),
        )

    async def save_user(self, record: UserRecord) -> None:
        payload = json.dumps(
            {
                "email": record.email,
                "password_hash": record.password_hash,
                "created_at": record.created_at.isoformat(),
                "user_type": record.user_type,
            }
        )
        async with self._lock:
            await self._redis.hset(self._key, record.email, payload)

    async def clear(self) -> None:
        async with self._lock:
            await self._redis.delete(self._key)


class SessionManager:
    """管理登录会话令牌的工具。"""

    def __init__(self) -> None:
        self._tokens: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_session(self, email: str) -> str:
        token = uuid.uuid4().hex
        async with self._lock:
            self._tokens[token] = email
        return token

    async def get_email(self, token: str) -> Optional[str]:
        async with self._lock:
            return self._tokens.get(token)

    async def clear(self) -> None:
        async with self._lock:
            self._tokens.clear()


class UserProfile(BaseModel):
    email: str
    created_at: datetime
    user_type: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        validate_email(value)
        return value

    @field_validator("user_type")
    @classmethod
    def _validate_user_type(cls, value: str) -> str:
        return validate_user_type(value, allow_admin=True)


class UserManager:
    """对外暴露用户注册、认证以及会话管理能力。"""

    def __init__(self, store: UserStore) -> None:
        self._store = store
        self._sessions = SessionManager()
        self._admin_seeded = False

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    async def _ensure_admin_exists(self) -> None:
        if self._admin_seeded:
            return
        normalized_email = self._normalize_email(DEFAULT_ADMIN_EMAIL)
        existing = await self._store.get_user(normalized_email)
        if existing is None:
            record = UserRecord(
                email=normalized_email,
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                created_at=datetime.now(timezone.utc),
                user_type=validate_user_type(ADMIN_USER_TYPE, allow_admin=True),
            )
            await self._store.save_user(record)
        self._admin_seeded = True

    async def register_user(
        self, email: str, password: str, user_type: str
    ) -> UserProfile:
        await self._ensure_admin_exists()
        validate_email(email)
        normalized = self._normalize_email(email)
        normalized_type = validate_user_type(user_type)
        existing = await self._store.get_user(normalized)
        if existing is not None:
            raise UserAlreadyExistsError("Email already registered")

        record = UserRecord(
            email=normalized,
            password_hash=hash_password(password),
            created_at=datetime.now(timezone.utc),
            user_type=normalized_type,
        )
        await self._store.save_user(record)
        return UserProfile(
            email=record.email, created_at=record.created_at, user_type=record.user_type
        )

    async def authenticate_user(self, email: str, password: str) -> str:
        await self._ensure_admin_exists()
        validate_email(email)
        normalized = self._normalize_email(email)
        record = await self._store.get_user(normalized)
        if record is None:
            raise AuthenticationError("Invalid email or password")
        if not verify_password(password, record.password_hash):
            raise AuthenticationError("Invalid email or password")
        return await self._sessions.create_session(normalized)

    async def reset(self) -> None:
        await self._store.clear()
        await self._sessions.clear()
        self._admin_seeded = False
        await self._ensure_admin_exists()


__all__ = [
    "UserManager",
    "UserProfile",
    "UserAlreadyExistsError",
    "AuthenticationError",
    "InMemoryUserStore",
    "RedisUserStore",
    "PUBLIC_USER_TYPES",
]

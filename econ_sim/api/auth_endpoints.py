"""用户注册与登录相关的 FastAPI 接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from ..auth import user_manager
from ..auth.user_manager import AuthenticationError, UserAlreadyExistsError
from ..auth.validators import validate_email

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        validate_email(value)
        return value


class RegisterResponse(BaseModel):
    user_id: str
    message: str


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        validate_email(value)
        return value


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = Field(default="bearer")


@router.post(
    "/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(payload: RegisterRequest) -> RegisterResponse:
    """注册新用户，默认使用邮箱作为唯一标识。"""

    try:
        profile = await user_manager.register_user(payload.email, payload.password)
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return RegisterResponse(user_id=profile.email, message="Registration successful.")


@router.post("/login", response_model=LoginResponse)
async def login_user(payload: LoginRequest) -> LoginResponse:
    """校验邮箱密码并返回简易会话令牌。"""

    try:
        token = await user_manager.authenticate_user(payload.email, payload.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    return LoginResponse(access_token=token)

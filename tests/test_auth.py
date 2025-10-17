import pytest
from httpx import ASGITransport, AsyncClient

from econ_sim.auth import user_manager
from econ_sim.auth.user_manager import (
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_BASELINE_PASSWORD,
    DEFAULT_BASELINE_USERS,
)
from econ_sim.main import app
from econ_sim.script_engine import script_registry


@pytest.mark.asyncio
# 测试：用户可以注册并通过登录接口获取有效的访问 token（access token 为非空字符串）。
async def test_user_registration_and_login() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/register",
            json={
                "email": "player@example.com",
                "password": "StrongPass123",
                "user_type": "individual",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["user_id"] == "player@example.com"

        login = await client.post(
            "/auth/login",
            json={"email": "player@example.com", "password": "StrongPass123"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        assert isinstance(token, str) and len(token) > 0


@pytest.mark.asyncio
# 测试：重复注册相同邮箱应返回 409 冲突状态码，防止重复创建用户。
async def test_duplicate_registration_returns_conflict() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/auth/register",
            json={
                "email": "dup@example.com",
                "password": "StrongPass123",
                "user_type": "firm",
            },
        )
        assert first.status_code == 201

        duplicate = await client.post(
            "/auth/register",
            json={
                "email": "dup@example.com",
                "password": "StrongPass123",
                "user_type": "firm",
            },
        )
        assert duplicate.status_code == 409


@pytest.mark.asyncio
# 测试：使用错误密码登录应返回 401 未授权响应。
async def test_login_with_wrong_password_fails() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={
                "email": "wrong@example.com",
                "password": "StrongPass123",
                "user_type": "government",
            },
        )
        bad = await client.post(
            "/auth/login",
            json={"email": "wrong@example.com", "password": "BadPass!"},
        )
        assert bad.status_code == 401


@pytest.mark.asyncio
# 测试：传递无效的 user_type 到注册接口应被验证拦截并返回 422。
async def test_register_with_invalid_user_type() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        invalid = await client.post(
            "/auth/register",
            json={
                "email": "invalid@example.com",
                "password": "StrongPass123",
                "user_type": "unknown",
            },
        )
        assert invalid.status_code == 422


@pytest.mark.asyncio
# 测试：默认管理员凭证应能成功登录并返回 200 状态码。
async def test_default_admin_can_login() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            json={
                "email": DEFAULT_ADMIN_EMAIL,
                "password": DEFAULT_ADMIN_PASSWORD,
            },
        )
        assert response.status_code == 200


@pytest.mark.asyncio
# 测试：基线用户集合应已被创建，使用默认基线密码登录应成功。
async def test_baseline_users_seeded() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for email, _ in DEFAULT_BASELINE_USERS:
            response = await client.post(
                "/auth/login",
                json={
                    "email": email,
                    "password": DEFAULT_BASELINE_PASSWORD,
                },
            )
            assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_baseline_scripts_seeded_automatically() -> None:
    # 清理已有基线脚本，确保测试环境明确。
    for email, _ in DEFAULT_BASELINE_USERS:
        await script_registry.remove_scripts_by_user(email)

    await user_manager.reset()

    for email, _ in DEFAULT_BASELINE_USERS:
        scripts = await script_registry.list_user_scripts(email)
        assert scripts, f"{email} should have at least one baseline script"
        latest = scripts[-1]
        assert latest.description and latest.description.startswith("[baseline]")

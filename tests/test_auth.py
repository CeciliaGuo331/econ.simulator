import pytest
from httpx import ASGITransport, AsyncClient

from econ_sim.auth import user_manager
from econ_sim.main import app


@pytest.mark.asyncio
async def test_user_registration_and_login() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/register",
            json={"email": "player@example.com", "password": "StrongPass123"},
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
async def test_duplicate_registration_returns_conflict() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "StrongPass123"},
        )
        assert first.status_code == 201

        duplicate = await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "StrongPass123"},
        )
        assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_login_with_wrong_password_fails() -> None:
    await user_manager.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={"email": "wrong@example.com", "password": "StrongPass123"},
        )
        bad = await client.post(
            "/auth/login",
            json={"email": "wrong@example.com", "password": "BadPass!"},
        )
        assert bad.status_code == 401

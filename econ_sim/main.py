"""宏观经济仿真服务的 FastAPI 入口模块。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api.auth_endpoints import router as auth_router
from .api.endpoints import router as simulation_router
from .web.views import router as web_router

session_secret = os.getenv("ECON_SIM_SESSION_SECRET", "econ-sim-session-key")

app = FastAPI(title="Econ Simulator", version="0.1.0")
app.add_middleware(SessionMiddleware, secret_key=session_secret)
app.include_router(simulation_router)
app.include_router(auth_router)
app.include_router(web_router)
static_dir = Path(__file__).resolve().parent / "web" / "static"
app.mount("/web/static", StaticFiles(directory=static_dir), name="web-static")


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """提供健康检查端点，供运行时监控使用。"""
    return {"status": "ok"}

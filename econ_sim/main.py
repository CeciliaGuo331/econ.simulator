"""宏观经济仿真服务的 FastAPI 入口模块。"""

from __future__ import annotations

from fastapi import FastAPI

from .api.endpoints import router as simulation_router

app = FastAPI(title="Econ Simulator", version="0.1.0")
app.include_router(simulation_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """提供健康检查端点，供运行时监控使用。"""
    return {"status": "ok"}

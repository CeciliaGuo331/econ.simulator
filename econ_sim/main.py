"""宏观经济仿真服务的 FastAPI 入口模块。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api.auth_endpoints import router as auth_router
from .api.endpoints import router as simulation_router, scripts_router
from .api.llm_endpoints import router as llm_router
from .web.views import router as web_router, _orchestrator as web_orchestrator

logger = logging.getLogger(__name__)

session_secret = os.getenv("ECON_SIM_SESSION_SECRET", "econ-sim-session-key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时可选地为测试世界播种，关闭时清理数据库连接池。

    使用 lifespan 可避免 `@app.on_event("startup")` 的弃用警告。
    """
    # startup
    try:
        skip_flag = os.getenv("ECON_SIM_SKIP_TEST_WORLD_SEED", "").lower()
        skip = skip_flag in {"1", "true", "yes", "on"} or os.getenv("PYTEST_CURRENT_TEST")
        if not skip:
            from .script_engine.test_world_seed import seed_test_world

            await seed_test_world(orchestrator=web_orchestrator)
            logger.info("test_world simulation seeded (auto-startup).")
        else:
            logger.info("Skipping test_world auto-seed (flag enabled or pytest).")
    except Exception:  # pragma: no cover - best effort logging
        logger.exception("Failed to seed test_world simulation during startup")

    # hand over to app runtime
    yield

    # shutdown
    try:
        from .data_access.postgres_support import close_all_pools

        await close_all_pools()
    except Exception:  # pragma: no cover - best effort cleanup
        pass


app = FastAPI(title="Econ Simulator", version="0.1.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=session_secret)

# Routers
app.include_router(simulation_router)
app.include_router(scripts_router)
app.include_router(llm_router)
app.include_router(auth_router)
app.include_router(web_router)

# Static
static_dir = Path(__file__).resolve().parent / "web" / "static"
app.mount("/web/static", StaticFiles(directory=static_dir), name="web-static")


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """提供健康检查端点，供运行时监控使用。"""
    return {"status": "ok"}

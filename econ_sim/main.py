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
from .api import endpoints as api_endpoints_module
from .api.endpoints import router as simulation_router, scripts_router
from .api.llm_endpoints import router as llm_router
from .web import views as web_views_module
from .web.views import router as web_router

logger = logging.getLogger(__name__)

session_secret = os.getenv("ECON_SIM_SESSION_SECRET", "econ-sim-session-key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时可选地为测试世界播种，关闭时清理数据库连接池。

    使用 lifespan 可避免 `@app.on_event("startup")` 的弃用警告。
    """
    # startup
    try:
        # Create background job manager and inject into views. Orchestrator
        # instances are created per-simulation on demand by the factory so we
        # do not create a global shared orchestrator here.
        from .core.orchestrator_factory import get_orchestrator
        from .web.background import BackgroundJobManager

        _shared_background = BackgroundJobManager()
        web_views_module._background_jobs = _shared_background

        logger.info("--- Background jobs created and injected ---")

        skip_flag = os.getenv("ECON_SIM_SKIP_TEST_WORLD_SEED", "").lower()
        skip = skip_flag in {"1", "true", "yes", "on"} or os.getenv(
            "PYTEST_CURRENT_TEST"
        )
        if not skip:
            from .script_engine.test_world_seed import seed_test_world
            from .script_engine.baseline_seed import ensure_baseline_scripts
            from .script_engine import script_registry as module_registry

            # Use the orchestrator factory to get the test_world orchestrator
            # and seed it. This creates a per-simulation orchestrator instance
            # keyed by "test_world".
            orch = await get_orchestrator("test_world")
            # Provide a module-level orchestrator reference for existing
            # modules and tests that expect `api.endpoints._orchestrator`
            # or `web.views._orchestrator` to be available.
            api_endpoints_module._orchestrator = orch
            web_views_module._orchestrator = orch

            await seed_test_world(orchestrator=orch)
            logger.info("test_world simulation seeded (auto-startup).")

            # Ensure baseline scripts/users are registered and attached to the
            # test_world simulation so scripts and entities are created together.
            try:
                await ensure_baseline_scripts(
                    module_registry, attach_to_simulation="test_world"
                )
                logger.info("baseline scripts ensured and attached to test_world.")
            except Exception:
                logger.exception("Failed to ensure baseline scripts during startup")
        else:
            logger.info("Skipping test_world auto-seed (flag enabled or pytest).")
    except Exception:  # pragma: no cover - best effort logging
        logger.exception("Failed to seed test_world simulation during startup")

    # hand over to app runtime
    yield

    # shutdown
    try:
        from .data_access.postgres_support import close_all_pools

        # Attempt graceful shutdown: stop background jobs and close DB pools.
        try:
            if _shared_background:
                await _shared_background.shutdown()
        except Exception:
            logger.exception("Error shutting down background job manager")

        await close_all_pools()

        # Remove injected references to avoid keeping state after shutdown
        try:
            web_views_module._background_jobs = None
            # If orchestrator_factory exposes a shutdown hook, call it.
            try:
                from .core.orchestrator_factory import shutdown_all

                await shutdown_all()
            except Exception:
                # best-effort: do not treat absence/failure as fatal
                pass
        except Exception:
            logger.exception("Failed to clear injected module references")
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

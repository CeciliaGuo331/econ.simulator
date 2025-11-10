"""
Econ Simulator 应用的入口模块（FastAPI）。

此模块负责应用级别的生命周期管理（lifespan）、路由挂载与
全局资源的启动/关闭。主要职责包括：

- 在应用启动时创建并注入共享的 DataAccessLayer（用于复用 Postgres/Redis 连接池），
    并将其传递给 orchestrator factory 以避免每个仿真实例重复创建连接池。
- 可选地在启动时自动播种教学世界（`test_world`），并确保基线脚本注册与附加。
- 在应用关闭时，优雅地停止后台任务、关闭采样器并尝试关闭所有数据库连接池。

重要环境变量（常用）：
- ECON_SIM_SESSION_SECRET：Session 中间件的 secret（用于 Cookie 等签名）。
- ECON_SIM_SKIP_TEST_WORLD_SEED：若设为 1/true/yes/on 则跳过自动播种流程（适用于 CI/某些生产场景）。

技术要点：
- 使用 FastAPI 的 lifespan 语义来集中管理启动/关闭逻辑，避免模块导入时产生副作用。
- 将共享后台任务管理器与 DAL 注入到 web 视图和 orchestrator factory，以使运行时组件能共享相同的资源句柄。

注意事项：
- 本 docstring 仅为模块说明；具体实现仍保留在函数体内，修改时请保持对生命周期流程的幂等性与可重试性。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# If detailed LLM logging is requested via env, ensure the
# 'econ_sim.llm' logger is at DEBUG so proxy INFO/DEBUG lines are emitted.
try:
    if os.getenv("ECON_SIM_LLM_LOG_FULL", "0") == "1":
        logging.getLogger("econ_sim.llm").setLevel(logging.DEBUG)
    else:
        # ensure at least INFO for llm previews when not in full mode
        logging.getLogger("econ_sim.llm").setLevel(logging.INFO)
except Exception:
    pass

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
        # Create background job manager and inject into views. Also create
        # a shared DataAccessLayer and inject it into the orchestrator factory
        # so all per-simulation orchestrators reuse DB/Redis pools.
        from .core.orchestrator_factory import get_orchestrator, init_shared_data_access
        from .web.background import BackgroundJobManager
        from .data_access.redis_client import DataAccessLayer

        _shared_background = BackgroundJobManager()
        web_views_module._background_jobs = _shared_background

        # Create shared DAL (will pick up env-configured Redis/Postgres)
        _shared_dal = DataAccessLayer.with_default_store()
        # start sampler for monitoring
        _shared_dal.start_sampler()
        # inject into factory so orchestrators reuse its pools/stores
        init_shared_data_access(_shared_dal)

        logger.info("--- Background jobs and shared DAL created and injected ---")

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

        # Attempt graceful shutdown: stop background jobs, stop DAL sampler and
        # close DB pools.
        try:
            if _shared_background:
                await _shared_background.shutdown()
        except Exception:
            logger.exception("Error shutting down background job manager")

        # Stop shared DAL sampler if present and close pools
        try:
            # Try to access the DAL via the orchestrator factory to avoid storing
            # another global reference here.
            from .core.orchestrator_factory import shutdown_all
            from .core.orchestrator_factory import _SHARED_DAL as _factory_dal

            if _factory_dal is not None:
                try:
                    _factory_dal.stop_sampler()
                except Exception:
                    logger.exception("Failed to stop DAL sampler")
        except Exception:
            logger.debug("Unable to stop shared DAL sampler via factory reference")

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

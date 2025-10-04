"""FastAPI application entrypoint for the economic simulator."""

from __future__ import annotations

from fastapi import FastAPI

from .api.endpoints import router as simulation_router

app = FastAPI(title="Econ Simulator", version="0.1.0")
app.include_router(simulation_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok"}

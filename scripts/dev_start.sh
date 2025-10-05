#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"
ENV_FILE=${ENV_FILE:-"${ROOT_DIR}/config/dev.env"}
APP_MODULE=${APP_MODULE:-"econ_sim.main:app"}
UVICORN_HOST=${UVICORN_HOST:-"0.0.0.0"}
UVICORN_PORT=${UVICORN_PORT:-"8000"}
UVICORN_RELOAD=${UVICORN_RELOAD:-"--reload"}
START_POSTGRES=${START_POSTGRES:-"1"}

start_infrastructure() {
  if [[ "${START_POSTGRES}" != "1" ]]; then
    echo "[dev-start] Skipping Docker infrastructure startup (START_POSTGRES=${START_POSTGRES})."
    return
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "[dev-start] Docker not found. Start services manually or install Docker."
    return
  fi

  local compose_cmd="docker compose"
  if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
      compose_cmd="docker-compose"
    else
      echo "[dev-start] Neither 'docker compose' nor 'docker-compose' is available. Start services manually."
      return
    fi
  fi

  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "[dev-start] docker-compose.yml not found at ${COMPOSE_FILE}."
    return
  fi

  local services="postgres redis"
  if [[ -n "${DOCKER_SERVICES:-}" ]]; then
    services="${DOCKER_SERVICES}"
  fi

  echo "[dev-start] Starting Docker services (${services}) via ${compose_cmd}..."
  ${compose_cmd} -f "${COMPOSE_FILE}" up -d ${services}
}

load_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    echo "[dev-start] Loading env vars from ${ENV_FILE}"
    set -a
    source "${ENV_FILE}"
    set +a
  else
    echo "[dev-start] Env file ${ENV_FILE} not found; continuing without it."
  fi
}

start_uvicorn() {
  cd "${ROOT_DIR}"
  echo "[dev-start] Launching Uvicorn (${APP_MODULE}) on ${UVICORN_HOST}:${UVICORN_PORT}"
  python -m uvicorn "${APP_MODULE}" ${UVICORN_RELOAD} --host "${UVICORN_HOST}" --port "${UVICORN_PORT}"
}

start_infrastructure
load_env_file
start_uvicorn

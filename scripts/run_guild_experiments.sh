#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
GUILD="${PROJECT_ROOT}/.guild-venv/bin/guild"
DOTENV="${PROJECT_ROOT}/.venv/bin/dotenv"
PROJECT_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
ENV_FILE="${PROJECT_ROOT}/.env"

if [ ! -x "${GUILD}" ]; then
  echo "Guild is not installed in .guild-venv" >&2
  exit 1
fi
if [ ! -x "${DOTENV}" ]; then
  echo "python-dotenv CLI is not installed in .venv" >&2
  exit 1
fi
if [ ! -x "${PROJECT_PYTHON}" ]; then
  echo "Project Python is not installed in .venv" >&2
  exit 1
fi
if [ ! -f "${ENV_FILE}" ]; then
  echo "Project .env is missing" >&2
  exit 1
fi

export GUILD_HOME="${PROJECT_ROOT}/.guild-home"
cd "${PROJECT_ROOT}"

if [ "${1:-}" = "run" ]; then
  shift
  exec "${DOTENV}" -f "${ENV_FILE}" run -- \
    "${GUILD}" run "$@" "project_python=${PROJECT_PYTHON}"
fi

exec "${GUILD}" "$@"

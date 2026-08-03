#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_DIR="${ROOT}/.local/laser-stack"
PYTHON="${ROOT}/.venv/bin/python"

cd "${ROOT}"

if [[ ! -x "${PYTHON}" ]] || ! "${PYTHON}" -c \
  'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
  uv venv --python 3.12 --clear
fi

uv pip install --python "${PYTHON}" -r "${ROOT}/requirements.txt"

LASER_MODE="$("${PYTHON}" - <<'PY'
from urllib.parse import urlsplit

from dotenv import dotenv_values

connection_string = (
    dotenv_values(".env").get("LASER_CONNECTION_STRING") or ""
).strip()
if not connection_string:
    raise SystemExit("LASER_CONNECTION_STRING is required in .env.")

local_hosts = {"localhost", "127.0.0.1", "::1"}
host = connection_string.lower().rstrip(".")
if host not in local_hosts:
    host = None
    for candidate in (connection_string, f"//{connection_string}"):
        try:
            parsed_host = urlsplit(candidate).hostname
        except ValueError:
            continue
        if parsed_host:
            host = parsed_host.lower().rstrip(".")
            break

print("local" if host in local_hosts else "remote")
PY
)"

if [[ "${LASER_MODE}" == "local" ]]; then
  docker info >/dev/null

  if [[ ! -d "${STACK_DIR}/.git" ]]; then
    mkdir -p "$(dirname "${STACK_DIR}")"
    git clone https://github.com/laserdata/laser-stack "${STACK_DIR}"
  fi

  "${STACK_DIR}/scripts/up"
else
  echo "Using configured LaserData Cloud deployment."
fi

"${PYTHON}" -m scripts.check_laser
"${PYTHON}" "${ROOT}/graph_seed.py"

echo "runbook dependencies are ready."

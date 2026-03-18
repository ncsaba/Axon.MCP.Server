#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source /home/vscode/.venv-dev/bin/activate
source "${ROOT_DIR}/scripts/dev_env.sh"

cd "${ROOT_DIR}"
exec uvicorn src.api.main:app --host "${API_HOST}" --port "${API_PORT}"

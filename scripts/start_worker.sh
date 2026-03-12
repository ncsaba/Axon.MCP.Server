#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source /home/vscode/.venv-axon-mcp/bin/activate
source "${ROOT_DIR}/scripts/dev_env.sh"

cd "${ROOT_DIR}"
exec celery -A src.workers.celery_app.celery_app worker \
  --loglevel=info \
  --queues=repository_sync,file_parsing,embeddings,ai_enrichment,repository_aggregation,default

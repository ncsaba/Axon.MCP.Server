#!/usr/bin/env bash
set -euo pipefail

cd /workspaces/axon-mcp/axon-src
source /home/vscode/.venv-dev/bin/activate
source scripts/dev_env.sh

export MCP_TRANSPORT="${MCP_TRANSPORT:-http}"
export MCP_HTTP_HOST="${MCP_HTTP_HOST:-0.0.0.0}"
export MCP_HTTP_PORT="${MCP_HTTP_PORT:-8001}"
export MCP_HTTP_PATH="${MCP_HTTP_PATH:-/mcp}"

exec python -m src.mcp_server.main

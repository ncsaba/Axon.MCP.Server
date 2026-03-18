# MCP Server Startup Guide

## Purpose

This is the canonical operational guide for starting Axon's MCP server in local development.

Use this guide when you want to:
- start the MCP server for stdio-based clients
- start the MCP server over HTTP for Codex app or direct terminal testing
- verify that the MCP endpoint is reachable

## Prerequisites

- devcontainer workspace at `/workspaces/axon-mcp`
- source repo at `/workspaces/axon-mcp/axon-src`
- Python virtualenv at `/home/vscode/.venv-dev`
- local dev env from `scripts/dev_env.sh`

## Recommended Startup Modes

| Mode | Use when | Command | Result |
| --- | --- | --- | --- |
| Stdio | A local MCP client launches the server process itself | `make mcp-start` | Starts the MCP server on stdio |
| HTTP | Codex app or manual HTTP testing should connect to a running endpoint | `make mcp-http-dev` | Starts the MCP HTTP server at `http://127.0.0.1:8001/mcp` |

## Start The MCP Server Over HTTP

Run from `/workspaces/axon-mcp/axon-src`:

```bash
source /home/vscode/.venv-dev/bin/activate
make mcp-http-dev
```

What this does:
- loads `scripts/dev_env.sh`
- sets `MCP_TRANSPORT=http`
- binds the server to `0.0.0.0:8001`
- exposes the MCP endpoint on `/mcp`

Important notes:
- `/mcp` is the stable path to use from clients
- the server also keeps `/mcp` available as a compatibility alias if the configured MCP path changes later
- empty MCP resources/templates are expected for this integration because Axon is primarily exposing MCP tools

## Start The MCP Server On Stdio

Run from `/workspaces/axon-mcp/axon-src`:

```bash
source /home/vscode/.venv-dev/bin/activate
make mcp-start
```

Use this when your MCP client starts Axon as a subprocess instead of connecting over HTTP.

## Verify The HTTP Endpoint

In a second terminal, run:

```bash
cd /workspaces/axon-mcp/axon-src
source /home/vscode/.venv-dev/bin/activate
make mcp-http-smoke
```

This checks:
- health endpoint
- MCP `initialize`
- MCP `tools/list`
- MCP `tools/call`

## Codex App Configuration

If the server is running in the devcontainer, use this in the host machine's `~/.codex/config.toml`:

```toml
[mcp_servers.axon]
url = "http://127.0.0.1:8001/mcp"
http_headers = { "X-API-Key" = "dev-admin-key" }
startup_timeout_sec = 20
tool_timeout_sec = 120
enabled = true
```

Notes:
- `dev-admin-key` is the local dev API key from `scripts/dev_env.sh`
- port `8001` must be forwarded from the devcontainer to the host
- after changing Codex MCP config, start a fresh session if tool discovery looks stale

## Common Problems

### `make api-dev` or `make mcp-http-dev` cannot find `uvicorn`

Activate the workspace virtualenv first:

```bash
source /home/vscode/.venv-dev/bin/activate
```

### Codex app connects but tools do not appear

Check these first:
- the server is running with `make mcp-http-dev`
- the configured URL is `http://127.0.0.1:8001/mcp`
- the API key header is `X-API-Key: dev-admin-key`
- you started a fresh Codex session after changing config

Server-side success signals in the logs:
- OAuth metadata probes may appear first
- then `initialize`
- then `notifications/initialized`
- then `tools/list`

### MCP resources/templates are empty

This is not a failure for Axon. The expected usable surface is the MCP tool list.

## Related Commands

Run from `/workspaces/axon-mcp/axon-src`:

```bash
make api-dev
make mcp-start
make mcp-http-dev
make mcp-http-smoke
make retrieval-smoke
```

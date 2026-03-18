"""MCP HTTP transport endpoint using the SDK's native streamable HTTP server."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from src.api.auth import get_current_user_mcp
from src.config.settings import get_settings
from src.mcp_server.server import mcp
from src.utils.logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter()

_mcp_http_session_manager: StreamableHTTPSessionManager | None = None


def create_mcp_http_session_manager() -> StreamableHTTPSessionManager:
    """Create a new SDK-native streamable HTTP session manager."""
    return StreamableHTTPSessionManager(
        app=mcp,
        json_response=True,
        stateless=False,
    )


def get_mcp_http_mount_path() -> str:
    """Return the normalized MCP HTTP mount path."""
    path = settings.mcp_http_path.strip()
    if not path:
        return "/mcp"
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") or "/mcp"


def get_mcp_http_mount_paths() -> list[str]:
    """Return all supported MCP HTTP mount paths, including the stable legacy alias."""
    configured_path = get_mcp_http_mount_path()
    paths = [configured_path]
    if configured_path != "/mcp":
        paths.append("/mcp")
    return paths


def set_mcp_http_session_manager(manager: StreamableHTTPSessionManager | None) -> None:
    """Set the active session manager for the mounted MCP HTTP app."""
    global _mcp_http_session_manager
    _mcp_http_session_manager = manager


def get_mcp_http_session_manager() -> StreamableHTTPSessionManager:
    """Return the active session manager or raise if HTTP MCP is not initialized."""
    if _mcp_http_session_manager is None:
        raise RuntimeError("MCP HTTP session manager is not initialized")
    return _mcp_http_session_manager


def _oauth_metadata() -> Dict[str, Any]:
    """Return minimal OAuth metadata for MCP client discovery probes."""
    issuer = f"http://{settings.mcp_http_host}:{settings.mcp_http_port}"
    return {
        "issuer": issuer,
        "authorization_endpoint": None,
        "token_endpoint": None,
        "registration_endpoint": None,
        "response_types_supported": [],
        "grant_types_supported": [],
        "token_endpoint_auth_methods_supported": [],
        "code_challenge_methods_supported": [],
    }


async def _authorize_mcp_request(request: Request) -> Dict[str, Any]:
    """Authorize an MCP HTTP request using the existing MCP auth policy."""
    return await get_current_user_mcp(
        api_key=request.headers.get("X-API-Key"),
        cookie_token=request.cookies.get("access_token"),
    )


class _MCPStreamableHTTPApp:
    """ASGI wrapper that applies auth, then delegates to the SDK manager."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)

        try:
            await _authorize_mcp_request(request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            await response(scope, receive, send)
            return

        try:
            manager = get_mcp_http_session_manager()
        except RuntimeError as exc:
            logger.error("mcp_http_manager_missing", error=str(exc))
            response = JSONResponse({"detail": str(exc)}, status_code=503)
            await response(scope, receive, send)
            return

        await manager.handle_request(scope, receive, send)


def mount_mcp_http_app(app: FastAPI, *, prefix: str = "") -> None:
    """Expose the MCP HTTP ASGI app at the configured MCP path."""
    normalized_prefix = prefix.rstrip("/")
    transport_app = _MCPStreamableHTTPApp()

    for mount_path in get_mcp_http_mount_paths():
        full_path = f"{normalized_prefix}{mount_path}" or "/mcp"
        route_slug = mount_path.strip("/").replace("/", "_") or "root"
        app.add_route(
            full_path,
            transport_app,
            methods=["GET", "POST", "DELETE"],
            include_in_schema=False,
            name=f"mcp_http_transport_{route_slug}",
        )
        app.add_route(
            f"{full_path}/",
            transport_app,
            methods=["GET", "POST", "DELETE"],
            include_in_schema=False,
            name=f"mcp_http_transport_{route_slug}_slash",
        )


@router.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server_root() -> Dict[str, Any]:
    """Compatibility endpoint for MCP clients probing OAuth metadata."""
    return _oauth_metadata()


@router.get("/.well-known/oauth-authorization-server/mcp")
async def oauth_authorization_server_root_mcp() -> Dict[str, Any]:
    """Compatibility endpoint for clients probing a path-specific issuer."""
    return _oauth_metadata()


@router.get(f"{get_mcp_http_mount_path()}/.well-known/oauth-authorization-server")
async def oauth_authorization_server_under_mcp() -> Dict[str, Any]:
    """Compatibility endpoint for Codex app MCP discovery."""
    return _oauth_metadata()


@router.get("/mcp/.well-known/oauth-authorization-server")
async def oauth_authorization_server_under_legacy_mcp() -> Dict[str, Any]:
    """Stable compatibility endpoint for the legacy /mcp Codex app path."""
    return _oauth_metadata()

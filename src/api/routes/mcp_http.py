"""MCP HTTP transport endpoint using the SDK's native streamable HTTP server."""

from __future__ import annotations

import json
from typing import Any, Dict

import anyio
from anyio.abc import TaskStatus
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from mcp.server.streamable_http import StreamableHTTPServerTransport
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
_RECOVERY_PROTOCOL_VERSION = "2025-06-18"
_RECOVERY_CLIENT_INFO = {
    "name": "axon-mcp-http-session-recovery",
    "version": "1.0.0",
}


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

        if scope.get("type") == "http" and scope.get("method") == "POST":
            body = await request.body()
            if await _maybe_recover_stale_session(manager, scope, body, send):
                return
            await manager.handle_request(scope, _make_receive(body), send)
            return

        await manager.handle_request(scope, receive, send)


def _make_receive(body: bytes) -> Receive:
    """Create a replayable ASGI receive callable for a buffered request body."""
    sent = False

    async def _receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return _receive


def _clone_scope(scope: Scope, *, headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    """Clone an HTTP scope with optional header replacement."""
    cloned = dict(scope)
    if headers is not None:
        cloned["headers"] = headers
    return cloned


def _append_recovery_header(send: Send) -> Send:
    """Wrap ASGI send to annotate recovered responses for tests and debugging."""

    async def _send(message: dict[str, Any]) -> None:
        if message.get("type") == "http.response.start":
            headers = list(message.get("headers", []))
            headers.append((b"x-axon-mcp-session-recovered", b"true"))
            message = dict(message)
            message["headers"] = headers
        await send(message)

    return _send


def _parse_jsonrpc_method(body: bytes) -> str | None:
    """Return the JSON-RPC method name for a buffered request body."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("method"), str):
        return payload["method"]
    return None


async def _maybe_recover_stale_session(
    manager: StreamableHTTPSessionManager,
    scope: Scope,
    body: bytes,
    send: Send,
) -> bool:
    """Recover a stale session by recreating the transport behind the same session id."""
    if manager.stateless:
        return False

    request = Request(scope, _make_receive(body))
    session_id = request.headers.get("mcp-session-id")
    if not session_id:
        return False
    if session_id in manager._server_instances:
        return False

    method = _parse_jsonrpc_method(body)
    if method is None:
        return False

    logger.warning(
        "mcp_http_stale_session_recovery_started",
        session_id=session_id,
        method=method,
        path=scope.get("path"),
    )

    try:
        transport = await _ensure_recovered_transport(manager, session_id)
        if method != "initialize":
            await _bootstrap_transport_initialize(transport, scope, session_id)
    except Exception as exc:
        logger.warning(
            "mcp_http_stale_session_recovery_failed",
            session_id=session_id,
            method=method,
            error=str(exc),
        )
        return False

    await transport.handle_request(scope, _make_receive(body), _append_recovery_header(send))
    logger.info(
        "mcp_http_stale_session_recovered",
        session_id=session_id,
        method=method,
    )
    return True


async def _ensure_recovered_transport(
    manager: StreamableHTTPSessionManager,
    session_id: str,
) -> StreamableHTTPServerTransport:
    """Create a replacement transport using the stale session id."""
    async with manager._session_creation_lock:
        existing = manager._server_instances.get(session_id)
        if existing is not None:
            return existing

        http_transport = StreamableHTTPServerTransport(
            mcp_session_id=session_id,
            is_json_response_enabled=manager.json_response,
            event_store=manager.event_store,
            security_settings=manager.security_settings,
            retry_interval=manager.retry_interval,
        )
        manager._server_instances[session_id] = http_transport

        async def run_server(*, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED) -> None:
            async with http_transport.connect() as streams:
                read_stream, write_stream = streams
                task_status.started()
                try:
                    await manager.app.run(
                        read_stream,
                        write_stream,
                        manager.app.create_initialization_options(),
                        stateless=False,
                    )
                except Exception as exc:  # pragma: no cover
                    logger.error(
                        "mcp_http_recovered_session_crashed",
                        session_id=session_id,
                        error=str(exc),
                        exc_info=True,
                    )
                finally:
                    if (
                        http_transport.mcp_session_id
                        and http_transport.mcp_session_id in manager._server_instances
                        and not http_transport.is_terminated
                    ):
                        del manager._server_instances[http_transport.mcp_session_id]

        if manager._task_group is None:
            raise RuntimeError("Task group is not initialized. Make sure to use run().")
        await manager._task_group.start(run_server)
        return http_transport


async def _bootstrap_transport_initialize(
    transport: StreamableHTTPServerTransport,
    scope: Scope,
    session_id: str,
) -> None:
    """Initialize a recovered transport so the next request can proceed normally."""
    init_payload = {
        "jsonrpc": "2.0",
        "id": "axon-session-recovery-init",
        "method": "initialize",
        "params": {
            "protocolVersion": _RECOVERY_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _RECOVERY_CLIENT_INFO,
        },
    }
    init_body = json.dumps(init_payload).encode("utf-8")

    async def _discard_send(_message: dict[str, Any]) -> None:
        return None

    await transport.handle_request(
        _clone_scope(scope),
        _make_receive(init_body),
        _discard_send,
    )


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

import asyncio
from dataclasses import dataclass

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp.types import TextContent

import src.api.routes.mcp_http as mcp_http_module
from src.api.auth import get_current_user
from src.api.routes.mcp_http import (
    create_mcp_http_session_manager,
    mount_mcp_http_app,
    router as mcp_http_router,
    set_mcp_http_session_manager,
)
from src.api.routes.mcp_test import router as mcp_test_router


@dataclass
class _Content:
    type: str
    text: str


def _initialize_request_payload():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {
                "name": "test-client",
                "version": "1.0",
            },
        },
    }


def _build_app(*routers):
    app = FastAPI()

    async def _fake_current_user():
        return {"user": "test", "role": "admin"}

    app.dependency_overrides[get_current_user] = _fake_current_user
    for router, prefix in routers:
        app.include_router(router, prefix=prefix)
        if router is mcp_http_router:
            mount_mcp_http_app(app, prefix=prefix)
    return app


def _request(app: FastAPI, method: str, path: str, **kwargs):
    async def _send():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(_send())


def _request_with_manager(app: FastAPI, method: str, path: str, **kwargs):
    async def _send():
        manager = create_mcp_http_session_manager()
        set_mcp_http_session_manager(manager)
        original_authorize = mcp_http_module._authorize_mcp_request

        async def _fake_authorize(_request):
            return {"user_id": "test"}

        mcp_http_module._authorize_mcp_request = _fake_authorize
        try:
            async with manager.run():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    return await client.request(method, path, **kwargs)
        finally:
            mcp_http_module._authorize_mcp_request = original_authorize
            set_mcp_http_session_manager(None)

    return asyncio.run(_send())


def _run_with_manager(app: FastAPI, runner):
    async def _run():
        manager = create_mcp_http_session_manager()
        set_mcp_http_session_manager(manager)
        original_authorize = mcp_http_module._authorize_mcp_request

        async def _fake_authorize(_request):
            return {"user_id": "test"}

        mcp_http_module._authorize_mcp_request = _fake_authorize
        try:
            async with manager.run():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    return await runner(client)
        finally:
            mcp_http_module._authorize_mcp_request = original_authorize
            set_mcp_http_session_manager(None)

    return asyncio.run(_run())


def test_mcp_http_initialize_returns_capabilities():
    app = _build_app((mcp_http_router, "/api/v1"))

    response = _request_with_manager(
        app,
        "POST",
        "/api/v1/mcp",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=_initialize_request_payload(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == 1
    assert payload["result"]["serverInfo"]["name"] == "axon-mcp-server"
    assert "mcp-session-id" in response.headers


def test_mcp_http_tools_list_works_after_initialize():
    app = _build_app((mcp_http_router, "/api/v1"))

    async def _scenario(client: AsyncClient):
        init_response = await client.post(
            "/api/v1/mcp",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=_initialize_request_payload(),
        )
        session_id = init_response.headers["mcp-session-id"]

        tools_response = await client.post(
            "/api/v1/mcp",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "mcp-session-id": session_id,
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        return tools_response

    response = _run_with_manager(app, _scenario)

    assert response.status_code == 200
    payload = response.json()
    assert "tools" in payload["result"]
    assert any(tool["name"] == "search_code" for tool in payload["result"]["tools"])
    assert any(tool["name"] == "find_repository_connections" for tool in payload["result"]["tools"])
    assert any(tool["name"] == "explain_repository_dependency" for tool in payload["result"]["tools"])


def test_mcp_http_tools_list_recovers_stale_session():
    app = _build_app((mcp_http_router, "/api/v1"))

    async def _scenario():
        manager = create_mcp_http_session_manager()
        set_mcp_http_session_manager(manager)
        original_authorize = mcp_http_module._authorize_mcp_request

        async def _fake_authorize(_request):
            return {"user_id": "test"}

        mcp_http_module._authorize_mcp_request = _fake_authorize
        try:
            async with manager.run():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    init_response = await client.post(
                        "/api/v1/mcp",
                        headers={"Accept": "application/json", "Content-Type": "application/json"},
                        json=_initialize_request_payload(),
                    )
                    session_id = init_response.headers["mcp-session-id"]
                    manager._server_instances.pop(session_id)

                    recovered_response = await client.post(
                        "/api/v1/mcp",
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "mcp-session-id": session_id,
                        },
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    )
                    follow_up_response = await client.post(
                        "/api/v1/mcp",
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "mcp-session-id": session_id,
                        },
                        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                    )
                    return recovered_response, follow_up_response
        finally:
            mcp_http_module._authorize_mcp_request = original_authorize
            set_mcp_http_session_manager(None)

    recovered_response, follow_up_response = asyncio.run(_scenario())

    assert recovered_response.status_code == 200
    assert recovered_response.headers["x-axon-mcp-session-recovered"] == "true"
    recovered_payload = recovered_response.json()
    assert any(tool["name"] == "search_code" for tool in recovered_payload["result"]["tools"])

    assert follow_up_response.status_code == 200
    assert "x-axon-mcp-session-recovered" not in follow_up_response.headers


def test_mcp_http_tools_call_recovers_stale_session(monkeypatch):
    app = _build_app((mcp_http_router, "/api/v1"))

    import src.mcp_server.tools.router as tool_router

    async def _fake_search_code(**kwargs):
        assert kwargs["query"] == "billing"
        return [TextContent(type="text", text="recovered tool call")]

    original_handler = tool_router.TOOL_HANDLERS["search_code"]
    tool_router.TOOL_HANDLERS["search_code"] = _fake_search_code

    async def _scenario():
        manager = create_mcp_http_session_manager()
        set_mcp_http_session_manager(manager)
        original_authorize = mcp_http_module._authorize_mcp_request

        async def _fake_authorize(_request):
            return {"user_id": "test"}

        mcp_http_module._authorize_mcp_request = _fake_authorize
        try:
            async with manager.run():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    init_response = await client.post(
                        "/api/v1/mcp",
                        headers={"Accept": "application/json", "Content-Type": "application/json"},
                        json=_initialize_request_payload(),
                    )
                    session_id = init_response.headers["mcp-session-id"]
                    manager._server_instances.pop(session_id)

                    return await client.post(
                        "/api/v1/mcp",
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "mcp-session-id": session_id,
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": 4,
                            "method": "tools/call",
                            "params": {
                                "name": "search_code",
                                "arguments": {"query": "billing", "limit": 1},
                            },
                        },
                    )
        finally:
            mcp_http_module._authorize_mcp_request = original_authorize
            set_mcp_http_session_manager(None)

    try:
        response = asyncio.run(_scenario())
    finally:
        tool_router.TOOL_HANDLERS["search_code"] = original_handler

    assert response.status_code == 200
    assert response.headers["x-axon-mcp-session-recovered"] == "true"
    payload = response.json()
    assert payload["result"]["content"][0]["text"] == "recovered tool call"


def test_mcp_http_notification_is_accepted():
    app = _build_app((mcp_http_router, "/api/v1"))

    async def _scenario(client: AsyncClient):
        init_response = await client.post(
            "/api/v1/mcp",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=_initialize_request_payload(),
        )
        session_id = init_response.headers["mcp-session-id"]

        notification_response = await client.post(
            "/api/v1/mcp",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "mcp-session-id": session_id,
            },
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        return notification_response

    response = _run_with_manager(app, _scenario)

    assert response.status_code == 202
    assert response.text == ""


def test_mcp_http_oauth_metadata_compatibility_routes_exist():
    app = _build_app((mcp_http_router, "/api/v1"))

    for path in (
        "/api/v1/.well-known/oauth-authorization-server",
        "/api/v1/.well-known/oauth-authorization-server/mcp",
        "/api/v1/mcp/.well-known/oauth-authorization-server",
    ):
        response = _request(app, "GET", path)
        assert response.status_code == 200
        payload = response.json()
        assert "issuer" in payload
        assert payload["grant_types_supported"] == []


def test_mcp_test_search_code_endpoint(monkeypatch):
    app = _build_app((mcp_test_router, "/api/v1"))

    async def _fake_search_code(**kwargs):
        assert kwargs["query"] == "billing"
        return [_Content(type="text", text="ok")]

    import src.mcp_server.tools.search as search_tools

    monkeypatch.setattr(search_tools, "search_code", _fake_search_code)

    response = _request(
        app,
        "POST",
        "/api/v1/mcp/tools/search_code",
        json={"query": "billing", "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["isError"] is False
    assert payload["content"][0]["text"] == "ok"


def test_mcp_test_get_symbol_context_endpoint(monkeypatch):
    app = _build_app((mcp_test_router, "/api/v1"))

    async def _fake_get_symbol_context(**kwargs):
        assert kwargs["symbol_id"] == 41
        assert kwargs["include_relationships"] is True
        return [_Content(type="text", text="python symbol context")]

    import src.mcp_server.tools.symbols as symbol_tools

    monkeypatch.setattr(symbol_tools, "get_symbol_context", _fake_get_symbol_context)

    response = _request(
        app,
        "POST",
        "/api/v1/mcp/tools/get_symbol_context",
        json={"symbol_id": 41, "include_relationships": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["isError"] is False
    assert payload["content"][0]["text"] == "python symbol context"


def test_mcp_test_get_symbol_context_endpoint_handles_tool_error(monkeypatch):
    app = _build_app((mcp_test_router, "/api/v1"))

    async def _fake_get_symbol_context(**kwargs):
        raise RuntimeError("symbol lookup failed")

    import src.mcp_server.tools.symbols as symbol_tools

    monkeypatch.setattr(symbol_tools, "get_symbol_context", _fake_get_symbol_context)

    response = _request(
        app,
        "POST",
        "/api/v1/mcp/tools/get_symbol_context",
        json={"symbol_id": 41, "include_relationships": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["isError"] is True
    assert "symbol lookup failed" in payload["content"][0]["text"]

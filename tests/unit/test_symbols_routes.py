import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.auth import get_current_user
from src.api.dependencies import get_db_session
from src.api.routes.symbols import router as symbols_router
from src.api.schemas.symbols import RelationEdge, SymbolResponse, SymbolWithRelations
from src.config.enums import LanguageEnum, RelationTypeEnum, SymbolKindEnum


def _build_app():
    app = FastAPI()
    app.include_router(symbols_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}

    async def _fake_db_session():
        class _DummySession:
            pass

        yield _DummySession()

    app.dependency_overrides[get_db_session] = _fake_db_session
    return app


def _request(app: FastAPI, method: str, path: str, **kwargs):
    async def _send():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(_send())


def test_get_symbol_returns_payload(monkeypatch):
    app = _build_app()

    symbol = SymbolResponse(
        id=10,
        file_id=99,
        repository_id=3,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.FUNCTION,
        name="run_pipeline",
        start_line=4,
        end_line=10,
        created_at=datetime.now(UTC),
    )

    async def _fake_get_symbol(self, symbol_id: int):
        assert symbol_id == 10
        return symbol

    import src.api.services.symbol_service as symbol_service

    monkeypatch.setattr(symbol_service.SymbolService, "get_symbol", _fake_get_symbol)

    response = _request(app, "GET", "/api/v1/symbols/10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["language"] == "PYTHON"
    assert payload["kind"] == "FUNCTION"
    assert payload["name"] == "run_pipeline"


def test_list_symbols_returns_paginated_payload(monkeypatch):
    app = _build_app()

    symbol = SymbolResponse(
        id=10,
        file_id=99,
        repository_id=3,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.FUNCTION,
        name="run_pipeline",
        start_line=4,
        end_line=10,
        created_at=datetime.now(UTC),
    )

    async def _fake_list_symbols(self, **kwargs):
        assert kwargs["offset"] == 0
        assert kwargs["limit"] == 5
        assert kwargs["repository_id"] == 3
        return [symbol], 1

    import src.api.services.symbol_service as symbol_service

    monkeypatch.setattr(symbol_service.SymbolService, "list_symbols", _fake_list_symbols)

    response = _request(app, "GET", "/api/v1/symbols?limit=5&repository_id=3")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 5
    assert payload["offset"] == 0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == 10


def test_list_symbols_in_file_returns_paginated_payload(monkeypatch):
    app = _build_app()

    symbol = SymbolResponse(
        id=22,
        file_id=44,
        repository_id=3,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.FUNCTION,
        name="emit_metrics",
        start_line=14,
        end_line=20,
        created_at=datetime.now(UTC),
    )

    async def _fake_list_symbols(self, **kwargs):
        assert kwargs["offset"] == 0
        assert kwargs["limit"] == 10
        assert kwargs["file_id"] == 44
        assert kwargs["symbol_kind"] == SymbolKindEnum.FUNCTION
        return [symbol], 1

    import src.api.services.symbol_service as symbol_service

    monkeypatch.setattr(symbol_service.SymbolService, "list_symbols", _fake_list_symbols)

    response = _request(app, "GET", "/api/v1/files/44/symbols?limit=10&symbol_kind=FUNCTION")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "emit_metrics"


def test_get_symbol_relationships_not_found(monkeypatch):
    app = _build_app()

    async def _fake_get_symbol_with_relations(self, symbol_id: int):
        assert symbol_id == 88
        return None

    import src.api.services.symbol_service as symbol_service

    monkeypatch.setattr(
        symbol_service.SymbolService,
        "get_symbol_with_relations",
        _fake_get_symbol_with_relations,
    )

    response = _request(app, "GET", "/api/v1/symbols/88/relationships")

    assert response.status_code == 404
    assert response.json()["detail"] == "Symbol not found"


def test_get_symbol_relationships_returns_relation_edges(monkeypatch):
    app = _build_app()

    symbol = SymbolWithRelations(
        id=22,
        file_id=99,
        repository_id=3,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.FUNCTION,
        name="run_pipeline",
        start_line=4,
        end_line=10,
        created_at=datetime.now(UTC),
        relations=[
            RelationEdge(
                id=7,
                relation_type=RelationTypeEnum.CALLS,
                to_symbol_id=30,
                to_symbol_name="emit_metrics",
                to_symbol_kind=SymbolKindEnum.FUNCTION,
            )
        ],
    )

    async def _fake_get_symbol_with_relations(self, symbol_id: int):
        assert symbol_id == 22
        return symbol

    import src.api.services.symbol_service as symbol_service

    monkeypatch.setattr(
        symbol_service.SymbolService,
        "get_symbol_with_relations",
        _fake_get_symbol_with_relations,
    )

    response = _request(app, "GET", "/api/v1/symbols/22/relationships")

    assert response.status_code == 200
    payload = response.json()
    assert payload["relations"][0]["relation_type"] == "CALLS"
    assert payload["relations"][0]["to_symbol_name"] == "emit_metrics"

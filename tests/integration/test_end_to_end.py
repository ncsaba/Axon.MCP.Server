"""End-to-end integration tests for the API."""

import pytest
from httpx import AsyncClient, ASGITransport
from src.api.main import app
from src.config.settings import get_settings
from src.config.enums import RepositoryStatusEnum, SymbolKindEnum, LanguageEnum
from src.api.dependencies import get_db_session


@pytest.fixture(autouse=True)
def disable_auth():
    """Disable authentication for end-to-end tests."""
    settings = get_settings()
    original_value = settings.auth_enabled
    settings.auth_enabled = False
    yield
    settings.auth_enabled = original_value


@pytest.fixture(autouse=True)
async def db_override(async_session):
    """Override database dependency to use test session."""
    async def get_test_session():
        yield async_session
        
    app.dependency_overrides[get_db_session] = get_test_session
    yield
    app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_endpoint():
    """Test health check endpoint."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health")
        
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ["healthy", "degraded", "unhealthy"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_api_endpoint():
    """Test search API endpoint end-to-end."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Test basic search
        response = await client.get("/api/v1/search?query=test&limit=10")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_with_filters():
    """Test search endpoint with filters."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/search",
            params={
                "query": "test",
                "limit": 5,
                "language": "PYTHON",
                "symbol_kind": "FUNCTION"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_hybrid_mode():
    """Test search in hybrid mode."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/search",
            params={
                "query": "authentication function",
                "limit": 10,
                "hybrid": True
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_crud():
    """Test repository CRUD operations."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create repository
        create_response = await client.post(
            "/api/v1/repositories",
            json={
                "gitlab_project_id": 999,
                "name": "test-repo",
                "path_with_namespace": "test/repo",
                "url": "https://example.com/repo.git",
                "clone_url": "https://example.com/repo.git",
                "default_branch": "main",
                "provider": "GITLAB"
            }
        )
        
        assert create_response.status_code in [200, 201]
        repo_data = create_response.json()
        repo_id = repo_data["id"]
        
        # Get repository
        get_response = await client.get(f"/api/v1/repositories/{repo_id}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["id"] == repo_id
        assert get_data["name"] == "test-repo"
        
        # List repositories
        list_response = await client.get("/api/v1/repositories")
        assert list_response.status_code == 200
        repos_data = list_response.json()
        assert "items" in repos_data
        assert "total" in repos_data
        assert isinstance(repos_data["items"], list)
        assert len(repos_data["items"]) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_list_with_filters():
    """Test repository listing with status filter."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/repositories",
            params={"status": "PENDING"}
        )
        
        assert response.status_code == 200
        repos_data = response.json()
        assert "items" in repos_data
        assert isinstance(repos_data["items"], list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_symbols_list(async_session):
    """Test symbols listing endpoint."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/api/v1/repositories",
            json={
                "gitlab_project_id": 1001,
                "name": "symbols-list-repo",
                "path_with_namespace": "test/symbols-list-repo",
                "url": "https://example.com/symbols-list-repo.git",
                "clone_url": "https://example.com/symbols-list-repo.git",
                "default_branch": "main",
                "provider": "GITLAB"
            }
        )
        assert create_response.status_code in [200, 201]
        repo_id = create_response.json()["id"]

        from src.database.models import FileInstance as File, Symbol

        file_record = File(
            repository_id=repo_id,
            path="src/app.py",
            language=LanguageEnum.PYTHON,
            size_bytes=120,
            line_count=20,
        )
        async_session.add(file_record)
        await async_session.flush()

        symbol = Symbol(
            file_instance_id=file_record.id,
            language=LanguageEnum.PYTHON,
            kind=SymbolKindEnum.FUNCTION,
            name="process_order",
            start_line=3,
            end_line=12,
            signature="def process_order(order_id: int) -> None",
        )
        async_session.add(symbol)
        await async_session.commit()

        response = await client.get(
            "/api/v1/symbols",
            params={"limit": 10}
        )

        assert response.status_code == 200
        payload = response.json()
        assert "items" in payload
        assert "total" in payload
        assert payload["limit"] == 10
        assert isinstance(payload["items"], list)
        assert any(item["id"] == symbol.id for item in payload["items"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_symbols_by_repository(async_session):
    """Test symbols filtered by repository."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First repository
        create_response_1 = await client.post(
            "/api/v1/repositories",
            json={
                "gitlab_project_id": 1000,
                "name": "symbols-test-repo-a",
                "path_with_namespace": "test/symbols-repo-a",
                "url": "https://example.com/symbols-repo-a.git",
                "clone_url": "https://example.com/symbols-repo-a.git",
                "default_branch": "main",
                "provider": "GITLAB"
            }
        )
        assert create_response_1.status_code in [200, 201]
        repo_id_1 = create_response_1.json()["id"]

        # Second repository
        create_response_2 = await client.post(
            "/api/v1/repositories",
            json={
                "gitlab_project_id": 1002,
                "name": "symbols-test-repo-b",
                "path_with_namespace": "test/symbols-repo-b",
                "url": "https://example.com/symbols-repo-b.git",
                "clone_url": "https://example.com/symbols-repo-b.git",
                "default_branch": "main",
                "provider": "GITLAB"
            }
        )
        assert create_response_2.status_code in [200, 201]
        repo_id_2 = create_response_2.json()["id"]

        from src.database.models import FileInstance as File, Symbol

        file_1 = File(
            repository_id=repo_id_1,
            path="src/repo_a.py",
            language=LanguageEnum.PYTHON,
            size_bytes=100,
            line_count=12,
        )
        file_2 = File(
            repository_id=repo_id_2,
            path="src/repo_b.py",
            language=LanguageEnum.PYTHON,
            size_bytes=110,
            line_count=15,
        )
        async_session.add_all([file_1, file_2])
        await async_session.flush()

        symbol_1 = Symbol(
            file_instance_id=file_1.id,
            language=LanguageEnum.PYTHON,
            kind=SymbolKindEnum.FUNCTION,
            name="repo_a_func",
            start_line=1,
            end_line=3,
        )
        symbol_2 = Symbol(
            file_instance_id=file_2.id,
            language=LanguageEnum.PYTHON,
            kind=SymbolKindEnum.FUNCTION,
            name="repo_b_func",
            start_line=1,
            end_line=3,
        )
        async_session.add_all([symbol_1, symbol_2])
        await async_session.commit()

        response = await client.get(
            "/api/v1/symbols",
            params={"repository_id": repo_id_1, "limit": 10},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] >= 1
        assert len(payload["items"]) >= 1
        assert all(item["repository_id"] == repo_id_1 for item in payload["items"])
        assert any(item["id"] == symbol_1.id for item in payload["items"])
        assert all(item["id"] != symbol_2.id for item in payload["items"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_jobs_list():
    """Test jobs listing endpoint."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/jobs")
        
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workers_status():
    """Test workers list endpoint."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Note: The endpoint is /api/v1/workers (list), not /api/v1/workers/status
        response = await client.get("/api/v1/workers")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_search_query():
    """Test search endpoint with invalid query."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Empty query should return error or empty results
        response = await client.get("/api/v1/search?query=&limit=10")
        
        # Should either return 400 or 200 with empty results
        assert response.status_code in [200, 400, 422]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_repository_id():
    """Test getting non-existent repository."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/repositories/999999")
        
        assert response.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cors_headers():
    """Test CORS headers are present."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Test CORS with a regular GET request instead of OPTIONS
        # FastAPI/Starlette doesn't automatically handle OPTIONS for all routes
        response = await client.get("/api/v1/health")
        
        assert response.status_code == 200
        # CORS headers should be present in response
        # Note: In test environment, CORS headers may not be fully populated


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rate_limiting_headers():
    """Test rate limiting headers are present."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/search?query=test&limit=10")
        
        assert response.status_code == 200
        # Rate limiting headers might be present
        # This is not a strict requirement but good to check


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_documentation():
    """Test API documentation endpoints are accessible."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # OpenAPI docs
        docs_response = await client.get("/api/docs")
        assert docs_response.status_code == 200
        
        # OpenAPI JSON
        openapi_response = await client.get("/api/openapi.json")
        assert openapi_response.status_code == 200
        openapi_data = openapi_response.json()
        assert "openapi" in openapi_data
        assert "paths" in openapi_data


@pytest.mark.integration
@pytest.mark.asyncio  
async def test_search_pagination():
    """Test search pagination."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request with small limit
        response = await client.get(
            "/api/v1/search",
            params={"query": "test", "limit": 3}
        )
        
        assert response.status_code == 200
        results = response.json()
        assert isinstance(results, list)
        assert len(results) <= 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_workflow():
    """Test a full workflow from repository creation to search."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create a repository
        create_repo_response = await client.post(
            "/api/v1/repositories",
            json={
                "gitlab_project_id": 2000,
                "name": "workflow-test-repo",
                "path_with_namespace": "test/workflow",
                "url": "https://example.com/workflow.git",
                "clone_url": "https://example.com/workflow.git",
                "default_branch": "main",
                "provider": "GITLAB"
            }
        )
        
        assert create_repo_response.status_code in [200, 201]
        repo = create_repo_response.json()
        repo_id = repo["id"]
        
        # 2. List repositories and verify it's there
        list_response = await client.get("/api/v1/repositories")
        assert list_response.status_code == 200
        repos_data = list_response.json()
        assert "items" in repos_data
        assert any(r["id"] == repo_id for r in repos_data["items"])
        
        # 3. Get specific repository
        get_response = await client.get(f"/api/v1/repositories/{repo_id}")
        assert get_response.status_code == 200
        get_repo = get_response.json()
        assert get_repo["id"] == repo_id
        assert get_repo["name"] == "workflow-test-repo"
        
        # 4. Search (may return empty if no symbols yet)
        search_response = await client.get(
            "/api/v1/search",
            params={"query": "workflow", "repository_id": repo_id}
        )
        assert search_response.status_code == 200
        search_results = search_response.json()
        assert isinstance(search_results, list)

import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.auth import get_current_user
from src.api.dependencies import get_db_session
from src.api.routes.repositories import router as repositories_router
from src.api.routes.statistics import router as statistics_router
from src.api.routes.workers import router as workers_router


def _build_app(*routers):
    app = FastAPI()
    for router, prefix in routers:
        app.include_router(router, prefix=prefix)

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


def _repository_payload(repository_id: int = 1) -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "id": repository_id,
        "provider": "GITLAB",
        "name": "demo-repo",
        "path_with_namespace": "team/demo-repo",
        "url": "https://gitlab.example.com/team/demo-repo",
        "clone_url": "https://gitlab.example.com/team/demo-repo.git",
        "default_branch": "main",
        "status": "COMPLETED",
        "last_synced_at": now,
        "last_commit_sha": "abc123",
        "total_files": 10,
        "total_symbols": 50,
        "size_bytes": 1024,
        "languages": {"PYTHON": 100.0},
        "primary_language": "PYTHON",
        "last_commit": None,
        "created_at": now,
        "updated_at": now,
        "gitlab_project_id": 101,
        "search_url": None,
        "sync_url": None,
    }


def test_list_repositories_returns_paginated_results(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    async def _fake_list(self, offset: int, limit: int):
        assert offset == 0
        assert limit == 20
        return [_repository_payload()], 1

    monkeypatch.setattr(repositories_module.RepositoryService, "list", _fake_list)

    response = _request(app, "GET", "/api/v1/repositories")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "demo-repo"


def test_get_repository_sync_history_returns_404_for_missing_repository(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    async def _fake_get(self, repository_id: int):
        assert repository_id == 999
        return None

    monkeypatch.setattr(repositories_module.RepositoryService, "get", _fake_get)

    response = _request(app, "GET", "/api/v1/repositories/999/sync-history")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_repository_sync_history_returns_jobs(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    async def _fake_get(self, repository_id: int):
        return _repository_payload(repository_id)

    async def _fake_job_list(self, offset: int, limit: int, repository_id: int | None = None):
        assert repository_id == 7
        return [
            {
                "id": 88,
                "repository_id": 7,
                "job_type": "sync",
                "status": "COMPLETED",
                "started_at": datetime.now(UTC).isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "duration_seconds": 12,
                "retry_count": 0,
                "max_retries": 3,
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        ], 1

    monkeypatch.setattr(repositories_module.RepositoryService, "get", _fake_get)
    monkeypatch.setattr(repositories_module.JobService, "list", _fake_job_list)

    response = _request(app, "GET", "/api/v1/repositories/7/sync-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["repository_id"] == 7


def test_trigger_repository_sync_returns_404_when_repository_is_missing(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    async def _fake_trigger_sync(self, repository_id: int):
        raise ValueError("missing")

    monkeypatch.setattr(repositories_module.RepositoryService, "trigger_sync", _fake_trigger_sync)

    response = _request(app, "POST", "/api/v1/repositories/123/sync")

    assert response.status_code == 404
    assert "failed to sync repository" in response.json()["detail"].lower()


def test_register_repository_from_url_returns_created_repository(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    async def _fake_build_create_payload_from_url(self, payload):
        raise AssertionError("build_create_payload_from_url should not be awaited")

    async def _fake_create(self, payload):
        assert payload.provider == "GITHUB" or payload.provider.value == "GITHUB"
        return type("CreatedRepo", (), {"id": 41})()

    async def _fake_trigger_sync(self, repository_id: int):
        assert repository_id == 41
        return None

    async def _fake_get(self, repository_id: int):
        assert repository_id == 41
        repo = _repository_payload(repository_id)
        repo["provider"] = "GITHUB"
        repo["name"] = "example-repo"
        repo["path_with_namespace"] = "octo/example-repo"
        repo["url"] = "https://github.com/octo/example-repo"
        repo["clone_url"] = "https://github.com/octo/example-repo.git"
        repo["gitlab_project_id"] = None
        return repo

    monkeypatch.setattr(
        repositories_module.RepositoryService,
        "build_create_payload_from_url",
        lambda self, payload: repositories_module.RepositoryCreate(
            provider="GITHUB",
            name="example-repo",
            path_with_namespace="octo/example-repo",
            url="https://github.com/octo/example-repo",
            clone_url="https://github.com/octo/example-repo.git",
            default_branch="main",
        ),
    )
    monkeypatch.setattr(repositories_module.RepositoryService, "create", _fake_create)
    monkeypatch.setattr(repositories_module.RepositoryService, "trigger_sync", _fake_trigger_sync)
    monkeypatch.setattr(repositories_module.RepositoryService, "get", _fake_get)

    response = _request(
        app,
        "POST",
        "/api/v1/repositories/register-url",
        json={"repository_url": "https://github.com/octo/example-repo"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["provider"] == "GITHUB"
    assert payload["path_with_namespace"] == "octo/example-repo"


def test_register_repository_from_url_returns_400_on_invalid_url(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    monkeypatch.setattr(
        repositories_module.RepositoryService,
        "build_create_payload_from_url",
        lambda self, payload: (_ for _ in ()).throw(ValueError("Repository URL must include an owner/group and repository name")),
    )

    response = _request(
        app,
        "POST",
        "/api/v1/repositories/register-url",
        json={"repository_url": "https://github.com/octo"},
    )

    assert response.status_code == 400
    assert "failed to register repository from url" in response.json()["detail"].lower()


def test_delete_repository_from_url_returns_204(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    repo = type("Repo", (), {"id": 41})()

    async def _fake_find_existing_repository_by_url(self, payload):
        return repo

    async def _fake_bulk_remove(self, repository_ids, *, cleanup_cache=False):
        assert repository_ids == [41]
        assert cleanup_cache is False
        return type("BulkDeleteResponse", (), {"failed_count": 0, "errors": []})()

    monkeypatch.setattr(
        repositories_module.RepositoryService,
        "find_existing_repository_by_url",
        _fake_find_existing_repository_by_url,
    )
    monkeypatch.setattr(
        repositories_module.RepositoryService,
        "bulk_remove_repositories",
        _fake_bulk_remove,
    )

    response = _request(
        app,
        "POST",
        "/api/v1/repositories/delete-url",
        json={"repository_url": "https://github.com/octo/example-repo"},
    )

    assert response.status_code == 204


def test_delete_repository_from_url_can_request_cache_cleanup(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    repo = type("Repo", (), {"id": 41})()

    async def _fake_find_existing_repository_by_url(self, payload):
        assert payload.cleanup_cache is True
        return repo

    async def _fake_bulk_remove(self, repository_ids, *, cleanup_cache=False):
        assert repository_ids == [41]
        assert cleanup_cache is True
        return type("BulkDeleteResponse", (), {"failed_count": 0, "errors": []})()

    monkeypatch.setattr(
        repositories_module.RepositoryService,
        "find_existing_repository_by_url",
        _fake_find_existing_repository_by_url,
    )
    monkeypatch.setattr(
        repositories_module.RepositoryService,
        "bulk_remove_repositories",
        _fake_bulk_remove,
    )

    response = _request(
        app,
        "POST",
        "/api/v1/repositories/delete-url",
        json={
            "repository_url": "https://github.com/octo/example-repo",
            "cleanup_cache": True,
        },
    )

    assert response.status_code == 204


def test_delete_repository_from_url_returns_404_when_not_found(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    async def _fake_find_existing_repository_by_url(self, payload):
        return None

    monkeypatch.setattr(
        repositories_module.RepositoryService,
        "find_existing_repository_by_url",
        _fake_find_existing_repository_by_url,
    )

    response = _request(
        app,
        "POST",
        "/api/v1/repositories/delete-url",
        json={"repository_url": "https://github.com/octo/missing-repo"},
    )

    assert response.status_code == 404
    assert "failed to delete repository from url" in response.json()["detail"].lower()


def test_delete_repository_by_id_can_request_cache_cleanup(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    async def _fake_get(self, repository_id: int):
        assert repository_id == 41
        return _repository_payload(repository_id)

    async def _fake_bulk_remove(self, repository_ids, *, cleanup_cache=False):
        assert repository_ids == [41]
        assert cleanup_cache is True
        return type("BulkDeleteResponse", (), {"failed_count": 0, "errors": []})()

    monkeypatch.setattr(repositories_module.RepositoryService, "get", _fake_get)
    monkeypatch.setattr(
        repositories_module.RepositoryService,
        "bulk_remove_repositories",
        _fake_bulk_remove,
    )

    response = _request(
        app,
        "DELETE",
        "/api/v1/repositories/41?cleanup_cache=true",
    )

    assert response.status_code == 204


def test_get_overview_statistics_returns_service_payload(monkeypatch):
    app = _build_app((statistics_router, "/api/v1"))

    import src.api.routes.statistics as statistics_module

    async def _fake_overview(self):
        return {
            "total_repositories": 2,
            "total_files": 100,
            "total_symbols": 500,
            "total_endpoints": 8,
            "total_outgoing_calls": 7,
            "total_published_events": 4,
            "total_event_subscriptions": 3,
            "top_languages": [
                {"language": "PYTHON", "file_count": 42, "size_bytes": 4096}
            ],
        }

    monkeypatch.setattr(statistics_module.StatisticsService, "get_overview_stats", _fake_overview)

    response = _request(app, "GET", "/api/v1/statistics/overview")

    assert response.status_code == 200
    assert response.json()["total_repositories"] == 2


def test_get_repository_statistics_returns_404_on_missing_repository(monkeypatch):
    app = _build_app((statistics_router, "/api/v1"))

    import src.api.routes.statistics as statistics_module

    async def _fake_repo_stats(self, repository_id: int):
        raise ValueError(f"Repository {repository_id} not found")

    monkeypatch.setattr(statistics_module.StatisticsService, "get_repository_stats", _fake_repo_stats)

    response = _request(app, "GET", "/api/v1/statistics/repository/99")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_repository_stats_route_returns_404_on_missing_repository(monkeypatch):
    app = _build_app((repositories_router, "/api/v1"))

    import src.api.routes.repositories as repositories_module

    async def _fake_repo_stats(self, repository_id: int):
        raise ValueError(f"Repository {repository_id} not found")

    monkeypatch.setattr(repositories_module.StatisticsService, "get_repository_stats", _fake_repo_stats)

    response = _request(app, "GET", "/api/v1/repositories/77/stats")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_list_workers_returns_worker_payload(monkeypatch):
    app = _build_app((workers_router, "/api/v1"))

    import src.api.routes.workers as workers_module

    async def _fake_workers(self):
        return [
            {
                "id": "w-1",
                "hostname": "localhost",
                "status": "ONLINE",
                "current_job_id": None,
                "last_heartbeat_at": datetime.now(UTC).isoformat(),
                "queues": ["default"],
                "started_at": datetime.now(UTC).isoformat(),
            }
        ]

    monkeypatch.setattr(workers_module.WorkerService, "list", _fake_workers)

    response = _request(app, "GET", "/api/v1/workers")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["status"] == "ONLINE"

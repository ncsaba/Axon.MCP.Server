from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import pytest
from git import GitCommandError

from src.api.schemas.repositories import RepositoryCreate, RepositoryRegisterFromUrl
from src.api.services.repository_service import RepositoryService
from src.config.enums import RepositoryStatusEnum, SourceControlProviderEnum
from src.database.models import Repository
from src.gitlab.repository_manager import RepositoryManager
from src.workers import sync_worker


@pytest.mark.asyncio
async def test_bulk_add_repositories_accepts_git_and_github(async_session, monkeypatch):
    queued_repository_ids: list[int] = []

    class _DummyTask:
        def __init__(self, task_id: str):
            self.id = task_id

    monkeypatch.setattr(
        "src.api.services.repository_service.sync_repository.delay",
        lambda repository_id: queued_repository_ids.append(repository_id) or _DummyTask(f"task-{repository_id}"),
    )

    service = RepositoryService(async_session)
    response = await service.bulk_add_repositories(
        [
            RepositoryCreate(
                provider=SourceControlProviderEnum.GIT,
                name="generic-repo",
                path_with_namespace="team/generic-repo",
                url="https://git.example.com/team/generic-repo.git",
                clone_url="https://git.example.com/team/generic-repo.git",
                default_branch="main",
            ),
            RepositoryCreate(
                provider=SourceControlProviderEnum.GITHUB,
                name="github-repo",
                path_with_namespace="octo/github-repo",
                url="https://github.com/octo/github-repo",
                clone_url="https://github.com/octo/github-repo.git",
                default_branch="main",
            ),
        ]
    )

    await async_session.commit()

    repositories = (
        await async_session.execute(
            Repository.__table__.select().order_by(Repository.id)
        )
    ).all()

    assert response.added_count == 2
    assert response.failed_count == 0
    assert len(queued_repository_ids) == 2
    assert [row.provider for row in repositories] == ["GIT", "GITHUB"]
    assert all(row.gitlab_project_id is None for row in repositories)


def test_build_create_payload_from_github_url_derives_provider_and_clone_url():
    service = RepositoryService(SimpleNamespace())

    payload = service.build_create_payload_from_url(
        RepositoryRegisterFromUrl(
            repository_url="https://github.com/octo/example-repo",
        )
    )

    assert payload.provider == SourceControlProviderEnum.GITHUB
    assert payload.name == "example-repo"
    assert payload.path_with_namespace == "octo/example-repo"
    assert payload.url == "https://github.com/octo/example-repo"
    assert payload.clone_url == "https://github.com/octo/example-repo.git"
    assert payload.default_branch == "main"


def test_build_create_payload_from_generic_git_url_preserves_git_suffix():
    service = RepositoryService(SimpleNamespace())

    payload = service.build_create_payload_from_url(
        RepositoryRegisterFromUrl(
            repository_url="https://git.example.com/team/platform-service.git",
        )
    )

    assert payload.provider == SourceControlProviderEnum.GIT
    assert payload.name == "platform-service"
    assert payload.path_with_namespace == "team/platform-service"
    assert payload.url == "https://git.example.com/team/platform-service"
    assert payload.clone_url == "https://git.example.com/team/platform-service.git"


def test_repository_manager_builds_provider_specific_https_transport(monkeypatch):
    settings = SimpleNamespace(
        gitlab_token="gitlab-token",
        github_token="github-token",
        generic_git_username="ci-user",
        generic_git_token="generic-token",
        repo_cache_dir="/tmp/axon-cache",
    )
    monkeypatch.setattr("src.gitlab.repository_manager.get_settings", lambda: settings)

    manager = RepositoryManager(cache_dir="/tmp/axon-cache")

    gitlab_url, _, canonical_url = manager._build_git_transport(
        "https://gitlab.example.com/team/repo.git",
        SourceControlProviderEnum.GITLAB,
    )
    github_url, _, _ = manager._build_git_transport(
        "https://github.com/octo/repo.git",
        SourceControlProviderEnum.GITHUB,
    )
    generic_url, _, _ = manager._build_git_transport(
        "https://git.example.com/team/repo.git",
        SourceControlProviderEnum.GIT,
    )

    assert gitlab_url.startswith("https://oauth2:gitlab-token@")
    assert github_url.startswith("https://x-access-token:github-token@")
    assert generic_url.startswith("https://ci-user:generic-token@")
    assert canonical_url == "https://gitlab.example.com/team/repo.git"


def test_repository_manager_clone_retries_without_branch_when_requested_branch_is_missing(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        gitlab_token="",
        github_token="",
        generic_git_username=None,
        generic_git_token=None,
        repo_cache_dir=str(tmp_path),
    )
    monkeypatch.setattr("src.gitlab.repository_manager.get_settings", lambda: settings)

    manager = RepositoryManager(cache_dir=str(tmp_path))
    clone_calls = []
    origin = Mock()
    repo = Mock()
    repo.remotes.origin = origin

    def _fake_clone_from(repo_url, repo_path, **kwargs):
        clone_calls.append(kwargs)
        if kwargs.get("branch") == "main":
            Path(repo_path).mkdir(parents=True, exist_ok=True)
            raise GitCommandError(["git", "clone"], 1, stderr=b"Remote branch main not found")
        Path(repo_path).mkdir(parents=True, exist_ok=True)
        return repo

    monkeypatch.setattr("src.gitlab.repository_manager.Repo.clone_from", _fake_clone_from)

    result = manager._clone_repository(
        "https://github.com/octo/example-repo.git",
        tmp_path / "example-repo",
        "main",
        1,
        SourceControlProviderEnum.GITHUB,
    )

    assert result == tmp_path / "example-repo"
    assert len(clone_calls) == 2
    assert clone_calls[0]["branch"] == "main"
    assert "branch" not in clone_calls[1]


def test_cleanup_repository_cache_skips_local_directory_sources(monkeypatch):
    service = RepositoryService(SimpleNamespace())
    repo = SimpleNamespace(
        id=4,
        clone_url="file:///workspaces/axon-mcp/dasc-prediction-domain",
        url="file:///workspaces/axon-mcp/dasc-prediction-domain",
        path_with_namespace="local/dasc-prediction-domain",
    )

    cleanup_calls = []

    class _Manager:
        def cleanup_repository(self, cache_key: str):
            cleanup_calls.append(cache_key)

    monkeypatch.setattr("src.api.services.repository_service.RepositoryManager", lambda: _Manager())

    cleaned = service._cleanup_repository_cache(repo)

    assert cleaned is False
    assert cleanup_calls == []


def test_cleanup_repository_cache_removes_cached_git_checkout(monkeypatch):
    service = RepositoryService(SimpleNamespace())
    repo = SimpleNamespace(
        id=9,
        clone_url="https://github.com/octo/example-repo.git",
        url="https://github.com/octo/example-repo",
        path_with_namespace="octo/example-repo",
    )

    cleanup_calls = []

    class _Manager:
        def cleanup_repository(self, cache_key: str):
            cleanup_calls.append(cache_key)

    monkeypatch.setattr("src.api.services.repository_service.RepositoryManager", lambda: _Manager())

    cleaned = service._cleanup_repository_cache(repo)

    assert cleaned is True
    assert cleanup_calls == ["octo/example-repo"]


@pytest.mark.asyncio
async def test_poll_repositories_for_updates_enqueues_refreshable_repositories(async_session, monkeypatch):
    queued_repository_ids: list[int] = []

    async_session.add_all(
        [
            Repository(
                provider=SourceControlProviderEnum.GIT,
                name="pending-repo",
                path_with_namespace="team/pending-repo",
                url="https://git.example.com/team/pending-repo.git",
                clone_url="https://git.example.com/team/pending-repo.git",
                default_branch="main",
                status=RepositoryStatusEnum.PENDING,
            ),
            Repository(
                provider=SourceControlProviderEnum.GITHUB,
                name="completed-repo",
                path_with_namespace="team/completed-repo",
                url="https://github.com/team/completed-repo",
                clone_url="https://github.com/team/completed-repo.git",
                default_branch="main",
                status=RepositoryStatusEnum.COMPLETED,
            ),
            Repository(
                provider=SourceControlProviderEnum.GITLAB,
                gitlab_project_id=123,
                name="failed-repo",
                path_with_namespace="team/failed-repo",
                url="https://gitlab.example.com/team/failed-repo",
                clone_url="https://gitlab.example.com/team/failed-repo.git",
                default_branch="main",
                status=RepositoryStatusEnum.FAILED,
            ),
            Repository(
                provider=SourceControlProviderEnum.GIT,
                name="busy-repo",
                path_with_namespace="team/busy-repo",
                url="https://git.example.com/team/busy-repo.git",
                clone_url="https://git.example.com/team/busy-repo.git",
                default_branch="main",
                status=RepositoryStatusEnum.CLONING,
            ),
        ]
    )
    await async_session.commit()

    class _DummyTask:
        def __init__(self, task_id: str):
            self.id = task_id

    monkeypatch.setattr(
        sync_worker.sync_repository,
        "delay",
        lambda repository_id: queued_repository_ids.append(repository_id) or _DummyTask(f"task-{repository_id}"),
    )

    result = await sync_worker._poll_repositories_for_updates_async()

    assert result["status"] == "queued"
    assert result["repositories_enqueued"] == 3
    assert queued_repository_ids == [1, 2, 3]

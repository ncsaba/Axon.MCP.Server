from __future__ import annotations

from pathlib import Path
from typing import Iterable
from unittest.mock import AsyncMock

import pytest
from git import Repo
from sqlalchemy import select

from src.config.enums import (
    FileLifecycleStateEnum,
    LanguageEnum,
    RepositoryIndexRunStatusEnum,
    RepositoryStatusEnum,
    SourceControlProviderEnum,
)
from src.config.settings import get_settings
from src.database.models import Dependency, FileInstance as File, Repository, RepositoryIndexRun
from src.workers.file_worker import create_or_update_file
from src.workers.incremental_sync import IncrementalSyncWorker


pytestmark = pytest.mark.integration


def _init_git_repo(repo_path: Path) -> Repo:
    repo = Repo.init(repo_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Axon Test")
        config.set_value("user", "email", "axon-tests@example.com")
    return repo


def _write_file(repo_path: Path, relative_path: str, content: str) -> None:
    file_path = repo_path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def _commit_all(repo: Repo, message: str, paths: Iterable[str] | None = None):
    repo_path = Path(repo.working_tree_dir or ".")
    if paths is None:
        repo.git.add(A=True)
    else:
        normalized = [str(Path(path).as_posix()) for path in paths]
        existing = [path for path in normalized if (repo_path / path).exists()]
        removed = [path for path in normalized if not (repo_path / path).exists()]
        if existing:
            repo.index.add(existing)
        if removed:
            repo.index.remove(removed, working_tree=True)
    return repo.index.commit(message)


async def _create_repository(async_session, repo_path: Path, last_commit_sha: str | None) -> Repository:
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999501,
        name="incremental-sync-repo",
        path_with_namespace="integration/incremental-sync-repo",
        url=repo_path.as_uri(),
        clone_url=str(repo_path),
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
        last_commit_sha=last_commit_sha,
    )
    async_session.add(repo)
    await async_session.commit()
    await async_session.refresh(repo)
    return repo


async def _seed_file(async_session, repository_id: int, repo_path: Path, relative_path: str) -> File:
    file_record = await create_or_update_file(
        async_session,
        repository_id=repository_id,
        file_path=repo_path / relative_path,
        repo_path=repo_path,
        run_id=1,
    )
    await async_session.commit()
    await async_session.refresh(file_record)
    return file_record


def _build_worker(async_session, repo_path: Path) -> IncrementalSyncWorker:
    worker = IncrementalSyncWorker(async_session, repo_path.parent)
    worker.rebuild_relationships_for_files = AsyncMock()
    worker._run_post_parse_parity_stages = AsyncMock()
    return worker


@pytest.mark.asyncio
async def test_incremental_sync_returns_up_to_date_when_head_matches(async_session, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    git_repo = _init_git_repo(repo_path)
    _write_file(repo_path, "src/app.py", "def app() -> str:\n    return 'ok'\n")
    initial_commit = _commit_all(git_repo, "initial")

    repository = await _create_repository(async_session, repo_path, initial_commit.hexsha)
    worker = _build_worker(async_session, repo_path)

    result = await worker.sync_repository_incremental(repository.id, repository, repo_path)

    runs = (
        await async_session.execute(
            select(RepositoryIndexRun).where(RepositoryIndexRun.repository_id == repository.id)
        )
    ).scalars().all()

    assert result["status"] == "up_to_date"
    assert result["files_changed"] == 0
    assert runs == []
    worker.rebuild_relationships_for_files.assert_not_awaited()
    worker._run_post_parse_parity_stages.assert_not_awaited()


@pytest.mark.asyncio
async def test_incremental_sync_processes_added_and_modified_files(async_session, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    git_repo = _init_git_repo(repo_path)

    _write_file(repo_path, "src/existing.py", "def version() -> str:\n    return 'v1'\n")
    initial_commit = _commit_all(git_repo, "initial")

    repository = await _create_repository(async_session, repo_path, initial_commit.hexsha)
    existing_file = await _seed_file(async_session, repository.id, repo_path, "src/existing.py")
    original_hash = existing_file.content_hash

    _write_file(repo_path, "src/existing.py", "def version() -> str:\n    return 'v2'\n")
    _write_file(repo_path, "src/new_file.py", "def created() -> str:\n    return 'new'\n")
    latest_commit = _commit_all(git_repo, "modify and add")

    worker = _build_worker(async_session, repo_path)
    result = await worker.sync_repository_incremental(repository.id, repository, repo_path)

    await async_session.refresh(repository)
    refreshed_files = (
        await async_session.execute(
            select(File)
            .where(File.repository_id == repository.id)
            .order_by(File.path)
        )
    ).scalars().all()
    run = (
        await async_session.execute(
            select(RepositoryIndexRun).where(RepositoryIndexRun.repository_id == repository.id)
        )
    ).scalar_one()

    assert result == {
        "status": "success",
        "repository_id": repository.id,
        "commit": latest_commit.hexsha,
        "files_changed": 2,
        "files_processed": 2,
        "files_added": 1,
        "files_modified": 1,
        "files_deleted": 0,
    }
    assert repository.last_commit_sha == latest_commit.hexsha
    assert repository.total_files == 2
    assert run.status == RepositoryIndexRunStatusEnum.SUCCEEDED
    assert [file.path for file in refreshed_files] == ["src/existing.py", "src/new_file.py"]
    assert all(file.lifecycle_state == FileLifecycleStateEnum.ACTIVE for file in refreshed_files)
    assert refreshed_files[0].content_hash != original_hash
    assert refreshed_files[0].last_seen_run_id == run.run_id
    assert refreshed_files[1].language == LanguageEnum.PYTHON
    assert refreshed_files[1].last_seen_run_id == run.run_id

    worker.rebuild_relationships_for_files.assert_awaited_once()
    assert worker.rebuild_relationships_for_files.await_args.args[0] == repository.id
    assert set(worker.rebuild_relationships_for_files.await_args.args[1]) == {
        file.id for file in refreshed_files
    }
    worker._run_post_parse_parity_stages.assert_awaited_once()
    assert set(worker._run_post_parse_parity_stages.await_args.kwargs["changed_file_ids"]) == {
        file.id for file in refreshed_files
    }
    assert worker._run_post_parse_parity_stages.await_args.kwargs["removed_file_ids"] == []


@pytest.mark.asyncio
async def test_incremental_sync_marks_deleted_files_missing(async_session, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    git_repo = _init_git_repo(repo_path)

    _write_file(repo_path, "src/delete_me.py", "def doomed() -> str:\n    return 'bye'\n")
    initial_commit = _commit_all(git_repo, "initial")

    repository = await _create_repository(async_session, repo_path, initial_commit.hexsha)
    tracked_file = await _seed_file(async_session, repository.id, repo_path, "src/delete_me.py")

    (repo_path / "src" / "delete_me.py").unlink()
    latest_commit = _commit_all(git_repo, "delete file", paths=["src/delete_me.py"])

    worker = _build_worker(async_session, repo_path)
    result = await worker.sync_repository_incremental(repository.id, repository, repo_path)

    await async_session.refresh(repository)
    refreshed_file = await async_session.get(File, tracked_file.id)
    run = (
        await async_session.execute(
            select(RepositoryIndexRun).where(RepositoryIndexRun.repository_id == repository.id)
        )
    ).scalar_one()

    assert result == {
        "status": "success",
        "repository_id": repository.id,
        "commit": latest_commit.hexsha,
        "files_changed": 1,
        "files_processed": 0,
        "files_added": 0,
        "files_modified": 0,
        "files_deleted": 1,
    }
    assert repository.last_commit_sha == latest_commit.hexsha
    assert repository.total_files == 0
    assert refreshed_file.lifecycle_state == FileLifecycleStateEnum.MISSING
    assert refreshed_file.missing_since is not None
    assert refreshed_file.last_seen_run_id == 1
    assert run.status == RepositoryIndexRunStatusEnum.SUCCEEDED

    worker.rebuild_relationships_for_files.assert_awaited_once_with(repository.id, [])
    worker._run_post_parse_parity_stages.assert_awaited_once()
    assert worker._run_post_parse_parity_stages.await_args.kwargs["changed_file_ids"] == []
    assert worker._run_post_parse_parity_stages.await_args.kwargs["removed_file_ids"] == [tracked_file.id]


@pytest.mark.asyncio
async def test_incremental_sync_handles_rename_as_missing_plus_new_file(async_session, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    git_repo = _init_git_repo(repo_path)

    _write_file(repo_path, "src/old_name.py", "def renamed() -> str:\n    return 'same'\n")
    initial_commit = _commit_all(git_repo, "initial")

    repository = await _create_repository(async_session, repo_path, initial_commit.hexsha)
    old_file = await _seed_file(async_session, repository.id, repo_path, "src/old_name.py")

    new_path = repo_path / "src" / "new_name.py"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    (repo_path / "src" / "old_name.py").rename(new_path)
    latest_commit = _commit_all(git_repo, "rename file", paths=["src/old_name.py", "src/new_name.py"])

    worker = _build_worker(async_session, repo_path)
    result = await worker.sync_repository_incremental(repository.id, repository, repo_path)

    await async_session.refresh(repository)
    refreshed_files = (
        await async_session.execute(
            select(File)
            .where(File.repository_id == repository.id)
            .order_by(File.path)
        )
    ).scalars().all()
    run = (
        await async_session.execute(
            select(RepositoryIndexRun).where(RepositoryIndexRun.repository_id == repository.id)
        )
    ).scalar_one()

    assert result == {
        "status": "success",
        "repository_id": repository.id,
        "commit": latest_commit.hexsha,
        "files_changed": 1,
        "files_processed": 1,
        "files_added": 0,
        "files_modified": 1,
        "files_deleted": 0,
    }
    assert repository.last_commit_sha == latest_commit.hexsha
    assert repository.total_files == 1
    assert run.status == RepositoryIndexRunStatusEnum.SUCCEEDED
    assert [file.path for file in refreshed_files] == ["src/new_name.py", "src/old_name.py"]
    assert refreshed_files[0].lifecycle_state == FileLifecycleStateEnum.ACTIVE
    assert refreshed_files[0].last_seen_run_id == run.run_id
    assert refreshed_files[1].id == old_file.id
    assert refreshed_files[1].lifecycle_state == FileLifecycleStateEnum.MISSING
    assert refreshed_files[1].missing_since is not None

    worker.rebuild_relationships_for_files.assert_awaited_once_with(
        repository.id,
        [refreshed_files[0].id],
    )
    worker._run_post_parse_parity_stages.assert_awaited_once()
    assert worker._run_post_parse_parity_stages.await_args.kwargs["changed_file_ids"] == [
        refreshed_files[0].id
    ]
    assert worker._run_post_parse_parity_stages.await_args.kwargs["removed_file_ids"] == [old_file.id]


@pytest.mark.asyncio
async def test_incremental_sync_runs_live_dependency_refresh(async_session, tmp_path: Path, monkeypatch):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    git_repo = _init_git_repo(repo_path)

    _write_file(repo_path, "src/app.py", "def version() -> str:\n    return 'v1'\n")
    _write_file(repo_path, "requirements.txt", "fastapi==0.115.0\npydantic>=2.7\n")
    initial_commit = _commit_all(git_repo, "initial")

    repository = await _create_repository(async_session, repo_path, initial_commit.hexsha)
    await _seed_file(async_session, repository.id, repo_path, "src/app.py")

    _write_file(repo_path, "src/app.py", "def version() -> str:\n    return 'v2'\n")
    latest_commit = _commit_all(git_repo, "modify app only")

    worker = IncrementalSyncWorker(async_session, repo_path.parent)
    worker.rebuild_relationships_for_files = AsyncMock()
    worker._rebuild_import_relationships = AsyncMock()
    worker._rebuild_call_graph = AsyncMock()
    worker._refresh_api_endpoints = AsyncMock()
    worker._refresh_references = AsyncMock()
    worker._refresh_outgoing_calls_and_events = AsyncMock()
    dependency_refresh = AsyncMock(wraps=worker._refresh_dependencies)
    worker._refresh_dependencies = dependency_refresh
    worker._refresh_configuration = AsyncMock()
    worker._refresh_embeddings = AsyncMock()
    worker._detect_patterns = AsyncMock()
    worker._refresh_services = AsyncMock()
    worker._refresh_service_documentation = AsyncMock()
    worker._refresh_module_summaries = AsyncMock()

    settings = get_settings()
    monkeypatch.setattr(settings, "extract_imports", False)
    monkeypatch.setattr(settings, "build_call_graph", False)
    monkeypatch.setattr(settings, "extract_api_endpoints", False)
    monkeypatch.setattr(settings, "extract_dependencies", True)
    monkeypatch.setattr(settings, "extract_configuration", False)
    monkeypatch.setattr(settings, "detect_patterns", False)

    result = await worker.sync_repository_incremental(repository.id, repository, repo_path)

    dependencies = (
        await async_session.execute(
            select(Dependency)
            .where(Dependency.repository_id == repository.id)
            .order_by(Dependency.package_name)
        )
    ).scalars().all()

    assert result["status"] == "success"
    assert result["commit"] == latest_commit.hexsha
    assert dependency_refresh.await_count == 1
    assert [(dep.package_name, dep.package_version, dep.version_constraint) for dep in dependencies] == [
        ("fastapi", "0.115.0", "==0.115.0"),
        ("pydantic", None, ">=2.7"),
    ]
    assert all(dep.file_path == "requirements.txt" for dep in dependencies)

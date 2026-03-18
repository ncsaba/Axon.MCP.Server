from __future__ import annotations

import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.config.enums import (
    FileLifecycleStateEnum,
    JobStatusEnum,
    LanguageEnum,
    RepositoryIndexRunStatusEnum,
    RepositoryStatusEnum,
    SourceControlProviderEnum,
)
from src.database.models import FileInstance as File, Job, Repository, RepositoryIndexRun
from src.workers.sync_worker import _sync_repository_async


pytestmark = pytest.mark.integration


STEP_MODULES = [
    ("src.workers.pipeline.steps.clone_step", "CloneStep"),
    ("src.workers.pipeline.steps.discovery_step", "DiscoveryStep"),
    ("src.workers.pipeline.steps.parsing_step", "ParsingStep"),
    ("src.workers.pipeline.steps.api_extraction_step", "ApiExtractionStep"),
    ("src.workers.pipeline.steps.reference_building_step", "ReferenceBuildingStep"),
    ("src.workers.pipeline.steps.relationship_building_step", "RelationshipBuildingStep"),
    ("src.workers.pipeline.steps.import_resolution_step", "ImportResolutionStep"),
    ("src.workers.pipeline.steps.call_graph_step", "CallGraphStep"),
    ("src.workers.pipeline.steps.dependency_extraction_step", "DependencyExtractionStep"),
    ("src.workers.pipeline.steps.config_extraction_step", "ConfigExtractionStep"),
    ("src.workers.pipeline.steps.pattern_detection_step", "PatternDetectionStep"),
    ("src.workers.pipeline.steps.combined_extraction_step", "CombinedExtractionStep"),
    ("src.workers.pipeline.steps.embedding_step", "EmbeddingGenerationStep"),
    ("src.workers.pipeline.steps.service_detection_step", "ServiceDetectionStep"),
    ("src.workers.pipeline.steps.service_documentation_step", "ServiceDocumentationStep"),
]


class _FakePublisher:
    async def connect(self) -> None:
        return None

    async def clear_logs(self, repository_id: int) -> None:
        return None

    async def publish_log(self, repository_id: int, message: str, **kwargs) -> None:
        return None

    async def close(self) -> None:
        return None


class _FakeLock:
    @contextmanager
    def acquire(self, resource_key: str, timeout: int = 3600):
        yield True


class _FakeStep:
    def __init__(self, name: str, *, fail: bool = False):
        self.name = name
        self.depends_on: list[str] = []
        self.fail = fail

    async def run(self, context) -> None:
        if self.fail:
            raise RuntimeError(f"{self.name} failed intentionally")
        context.completed_steps.add(self.name)

    async def execute(self, context) -> None:
        if self.fail:
            raise RuntimeError(f"{self.name} failed intentionally")


def _install_fake_pipeline(monkeypatch: pytest.MonkeyPatch, failing_step: str | None = None) -> None:
    for module_name, class_name in STEP_MODULES:
        module = importlib.import_module(module_name)

        def _factory(step_name=class_name):
            return _FakeStep(step_name, fail=step_name == failing_step)

        monkeypatch.setattr(module, class_name, _factory)


async def _create_repository(async_session, gitlab_project_id: int) -> Repository:
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=gitlab_project_id,
        name=f"sync-lifecycle-{gitlab_project_id}",
        path_with_namespace=f"integration/sync-lifecycle-{gitlab_project_id}",
        url=f"https://example.com/integration/sync-lifecycle-{gitlab_project_id}.git",
        clone_url=f"https://example.com/integration/sync-lifecycle-{gitlab_project_id}.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.commit()
    return repo


def _build_task(task_id: str) -> SimpleNamespace:
    return SimpleNamespace(request=SimpleNamespace(id=task_id, retries=0))


async def _noop_async(*args, **kwargs):
    return 0


@pytest.mark.asyncio
async def test_sync_success_marks_stale_files_missing(async_session, monkeypatch):
    repo = await _create_repository(async_session, 999401)
    async_session.add_all(
        [
            File(
                repository_id=repo.id,
                path="src/stale.py",
                language=LanguageEnum.PYTHON,
                lifecycle_state=FileLifecycleStateEnum.ACTIVE,
                last_seen_run_id=0,
                size_bytes=10,
            ),
            File(
                repository_id=repo.id,
                path="src/current.py",
                language=LanguageEnum.PYTHON,
                lifecycle_state=FileLifecycleStateEnum.ACTIVE,
                last_seen_run_id=1,
                size_bytes=20,
            ),
        ]
    )
    await async_session.commit()

    _install_fake_pipeline(monkeypatch)
    monkeypatch.setattr("src.workers.sync_worker.RedisLogPublisher", _FakePublisher)
    monkeypatch.setattr("src.workers.sync_worker.get_distributed_lock", lambda: _FakeLock())
    monkeypatch.setattr("src.workers.sync_worker._generate_module_summaries", _noop_async)
    monkeypatch.setattr("src.workers.sync_worker.ParserFactory.cleanup", _noop_async)
    monkeypatch.setattr("src.workers.sync_worker.celery_app.send_task", lambda *args, **kwargs: None)

    result = await _sync_repository_async(_build_task("sync-success-1"), repo.id)

    await async_session.refresh(repo)
    refreshed_files = (
        await async_session.execute(
            select(File)
            .where(File.repository_id == repo.id)
            .order_by(File.path)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    job = (
        await async_session.execute(
            select(Job).where(Job.repository_id == repo.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    run = (
        await async_session.execute(
            select(RepositoryIndexRun)
            .where(RepositoryIndexRun.repository_id == repo.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    assert result["status"] == "success"
    assert repo.status == RepositoryStatusEnum.COMPLETED
    assert repo.total_files == 1
    assert [file.lifecycle_state for file in refreshed_files] == [
        FileLifecycleStateEnum.ACTIVE,
        FileLifecycleStateEnum.MISSING,
    ]
    assert refreshed_files[1].missing_since is not None
    assert job.status == JobStatusEnum.COMPLETED
    assert run.status == RepositoryIndexRunStatusEnum.SUCCEEDED


@pytest.mark.asyncio
async def test_sync_failure_does_not_mark_files_missing(async_session, monkeypatch):
    repo = await _create_repository(async_session, 999402)
    async_session.add(
        File(
            repository_id=repo.id,
            path="src/stale.py",
            language=LanguageEnum.PYTHON,
            lifecycle_state=FileLifecycleStateEnum.ACTIVE,
            last_seen_run_id=0,
            size_bytes=10,
        )
    )
    await async_session.commit()

    _install_fake_pipeline(monkeypatch, failing_step="CloneStep")
    monkeypatch.setattr("src.workers.sync_worker.RedisLogPublisher", _FakePublisher)
    monkeypatch.setattr("src.workers.sync_worker.get_distributed_lock", lambda: _FakeLock())
    monkeypatch.setattr("src.workers.sync_worker._generate_module_summaries", _noop_async)
    monkeypatch.setattr("src.workers.sync_worker.ParserFactory.cleanup", _noop_async)
    monkeypatch.setattr("src.workers.sync_worker.celery_app.send_task", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="CloneStep failed intentionally"):
        await _sync_repository_async(_build_task("sync-failure-1"), repo.id)

    await async_session.refresh(repo)
    refreshed_file = (
        await async_session.execute(
            select(File)
            .where(File.repository_id == repo.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    job = (
        await async_session.execute(
            select(Job).where(Job.repository_id == repo.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    run = (
        await async_session.execute(
            select(RepositoryIndexRun)
            .where(RepositoryIndexRun.repository_id == repo.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    assert refreshed_file.lifecycle_state == FileLifecycleStateEnum.ACTIVE
    assert refreshed_file.missing_since is None
    assert repo.status == RepositoryStatusEnum.FAILED
    assert job.status == JobStatusEnum.FAILED
    assert run.status == RepositoryIndexRunStatusEnum.FAILED

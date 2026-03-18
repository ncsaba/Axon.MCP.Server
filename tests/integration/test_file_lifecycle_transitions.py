from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import (
    FileLifecycleStateEnum,
    LanguageEnum,
    RepositoryStatusEnum,
    SourceControlProviderEnum,
)
from src.config.settings import get_settings
from src.database.models import File, FileContent, Repository
from src.workers.file_lifecycle_worker import _cleanup_missing_file_instances_async
from src.workers.file_worker import DEFAULT_PARSER_FINGERPRINT, create_or_update_file
from src.workers.sync_worker import _mark_missing_file_instances


pytestmark = pytest.mark.integration


async def _create_repository(async_session) -> Repository:
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999301,
        name="file-lifecycle-repo",
        path_with_namespace="integration/file-lifecycle-repo",
        url="https://example.com/integration/file-lifecycle-repo.git",
        clone_url="https://example.com/integration/file-lifecycle-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()
    return repo


@pytest.mark.asyncio
async def test_mark_missing_file_instances_marks_only_stale_active_rows(async_session):
    repo = await _create_repository(async_session)
    now = datetime.now(UTC)

    stale_active = File(
        repository_id=repo.id,
        path="src/stale.py",
        language=LanguageEnum.PYTHON,
        lifecycle_state=FileLifecycleStateEnum.ACTIVE,
        last_seen_run_id=1,
        last_seen_at=now - timedelta(days=1),
    )
    current_active = File(
        repository_id=repo.id,
        path="src/current.py",
        language=LanguageEnum.PYTHON,
        lifecycle_state=FileLifecycleStateEnum.ACTIVE,
        last_seen_run_id=2,
        last_seen_at=now,
    )
    never_seen = File(
        repository_id=repo.id,
        path="src/never-seen.py",
        language=LanguageEnum.PYTHON,
        lifecycle_state=FileLifecycleStateEnum.ACTIVE,
        last_seen_run_id=None,
    )
    already_missing = File(
        repository_id=repo.id,
        path="src/already-missing.py",
        language=LanguageEnum.PYTHON,
        lifecycle_state=FileLifecycleStateEnum.MISSING,
        last_seen_run_id=1,
        missing_since=now - timedelta(days=2),
    )
    async_session.add_all([stale_active, current_active, never_seen, already_missing])
    await async_session.commit()

    await _mark_missing_file_instances(async_session, repository_id=repo.id, current_run_id=2)
    await async_session.commit()

    refreshed = {
        file.path: file
        for file in (
            await async_session.execute(select(File).where(File.repository_id == repo.id))
        ).scalars()
    }

    assert refreshed["src/stale.py"].lifecycle_state == FileLifecycleStateEnum.MISSING
    assert refreshed["src/stale.py"].missing_since is not None
    assert refreshed["src/current.py"].lifecycle_state == FileLifecycleStateEnum.ACTIVE
    assert refreshed["src/never-seen.py"].lifecycle_state == FileLifecycleStateEnum.ACTIVE
    assert refreshed["src/already-missing.py"].lifecycle_state == FileLifecycleStateEnum.MISSING


@pytest.mark.asyncio
async def test_create_or_update_file_reactivates_missing_instance(async_session, tmp_path: Path):
    repo = await _create_repository(async_session)
    repo_path = tmp_path / "repo"
    file_path = repo_path / "src" / "reactivated.py"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("def reactivate() -> str:\n    return 'ok'\n", encoding="utf-8")

    missing_file = File(
        repository_id=repo.id,
        path="src/reactivated.py",
        language=LanguageEnum.PYTHON,
        lifecycle_state=FileLifecycleStateEnum.MISSING,
        missing_since=datetime.now(UTC) - timedelta(days=3),
        last_seen_run_id=3,
    )
    async_session.add(missing_file)
    await async_session.commit()

    updated = await create_or_update_file(
        async_session,
        repository_id=repo.id,
        file_path=file_path,
        repo_path=repo_path,
        run_id=4,
    )
    await async_session.commit()

    reloaded = await async_session.get(File, updated.id)
    assert reloaded is not None
    assert reloaded.id == missing_file.id
    assert reloaded.lifecycle_state == FileLifecycleStateEnum.ACTIVE
    assert reloaded.missing_since is None
    assert reloaded.last_seen_run_id == 4
    assert reloaded.current_content_id is not None

    content = await async_session.get(FileContent, reloaded.current_content_id)
    assert content is not None
    assert content.parser_fingerprint == DEFAULT_PARSER_FINGERPRINT


@pytest.mark.asyncio
async def test_cleanup_missing_file_instances_reclaims_newly_orphaned_content(
    async_session,
    monkeypatch,
):
    repo = await _create_repository(async_session)
    now = datetime.now(UTC)

    expired_content = FileContent(
        content_hash="expired-content",
        language=LanguageEnum.PYTHON,
        parser_fingerprint=DEFAULT_PARSER_FINGERPRINT,
        size_bytes=10,
        line_count=1,
    )
    retained_content = FileContent(
        content_hash="retained-content",
        language=LanguageEnum.PYTHON,
        parser_fingerprint=DEFAULT_PARSER_FINGERPRINT,
        size_bytes=10,
        line_count=1,
    )
    preexisting_orphan = FileContent(
        content_hash="orphan-content",
        language=LanguageEnum.PYTHON,
        parser_fingerprint=DEFAULT_PARSER_FINGERPRINT,
        size_bytes=10,
        line_count=1,
    )
    async_session.add_all([expired_content, retained_content, preexisting_orphan])
    await async_session.flush()

    expired_missing = File(
        repository_id=repo.id,
        path="src/expired.py",
        language=LanguageEnum.PYTHON,
        lifecycle_state=FileLifecycleStateEnum.MISSING,
        missing_since=now - timedelta(days=10),
        current_content_id=expired_content.id,
        content_hash=expired_content.content_hash,
    )
    retained_active = File(
        repository_id=repo.id,
        path="src/retained.py",
        language=LanguageEnum.PYTHON,
        lifecycle_state=FileLifecycleStateEnum.ACTIVE,
        current_content_id=retained_content.id,
        content_hash=retained_content.content_hash,
    )
    async_session.add_all([expired_missing, retained_active])
    await async_session.commit()

    monkeypatch.setenv("FILE_INSTANCE_MISSING_TTL_DAYS", "7")
    monkeypatch.setenv("FILE_INSTANCE_CLEANUP_BATCH_SIZE", "100")
    get_settings.cache_clear()
    try:
        result = await _cleanup_missing_file_instances_async(repository_id=repo.id)

        remaining_files = (
            await async_session.execute(select(File).where(File.repository_id == repo.id))
        ).scalars().all()
        remaining_contents = (
            await async_session.execute(select(FileContent).order_by(FileContent.id))
        ).scalars().all()
        remaining_content_hashes = {content.content_hash for content in remaining_contents}

        assert result["deleted_instances"] == 1
        assert result["deleted_contents"] == 2
        assert [file.path for file in remaining_files] == ["src/retained.py"]
        assert remaining_content_hashes == {"retained-content"}
    finally:
        get_settings.cache_clear()

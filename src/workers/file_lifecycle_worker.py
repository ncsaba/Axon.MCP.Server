"""Cleanup tasks for file instance lifecycle and orphan content reclamation."""

from __future__ import annotations

import asyncio
import time
import traceback
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from src.config.enums import FileLifecycleStateEnum
from src.config.settings import get_settings
from src.database.models import File, FileContent
from src.database.session import AsyncSessionLocal
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    file_content_cleanup_total,
    file_instance_cleanup_duration_seconds,
    file_instance_cleanup_total,
)
from src.workers.celery_app import celery_app
from src.workers.utils import _run_with_engine_cleanup

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="src.workers.file_lifecycle_worker.cleanup_missing_file_instances",
    max_retries=3,
)
def cleanup_missing_file_instances(self, repository_id: int | None = None) -> dict:
    """Delete expired missing file instances and reclaim orphaned file contents."""
    try:
        return asyncio.run(
            _run_with_engine_cleanup(
                _cleanup_missing_file_instances_async(repository_id=repository_id)
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "cleanup_missing_file_instances_failed",
            repository_id=repository_id,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


async def _cleanup_missing_file_instances_async(repository_id: int | None = None) -> dict:
    """Async cleanup implementation."""
    settings = get_settings()
    started = time.perf_counter()
    cutoff = datetime.now(UTC) - timedelta(days=settings.file_instance_missing_ttl_days)
    batch_size = max(1, settings.file_instance_cleanup_batch_size)

    async with AsyncSessionLocal() as session:
        instance_ids_stmt = (
            select(File.id)
            .where(
                File.lifecycle_state == FileLifecycleStateEnum.MISSING,
                File.missing_since.is_not(None),
                File.missing_since < cutoff,
            )
            .order_by(File.missing_since.asc())
            .limit(batch_size)
        )
        if repository_id is not None:
            instance_ids_stmt = instance_ids_stmt.where(File.repository_id == repository_id)

        expired_instance_ids = [int(row[0]) for row in (await session.execute(instance_ids_stmt)).all()]

        deleted_instances = 0
        deleted_contents = 0

        if expired_instance_ids:
            await session.execute(delete(File).where(File.id.in_(expired_instance_ids)))
            deleted_instances = len(expired_instance_ids)
            await session.flush()

        orphan_content_ids_stmt = (
            select(FileContent.id)
            .outerjoin(File, File.current_content_id == FileContent.id)
            .group_by(FileContent.id)
            .having(func.count(File.id) == 0)
            .limit(batch_size)
        )
        orphan_content_ids = [
            int(row[0]) for row in (await session.execute(orphan_content_ids_stmt)).all()
        ]

        if orphan_content_ids:
            await session.execute(delete(FileContent).where(FileContent.id.in_(orphan_content_ids)))
            deleted_contents = len(orphan_content_ids)

        await session.commit()

    duration = time.perf_counter() - started
    file_instance_cleanup_duration_seconds.labels(mode="ttl_cleanup").observe(duration)
    if deleted_instances:
        file_instance_cleanup_total.labels(result="deleted").inc(deleted_instances)
    else:
        file_instance_cleanup_total.labels(result="noop").inc()
    if deleted_contents:
        file_content_cleanup_total.labels(result="deleted").inc(deleted_contents)
    else:
        file_content_cleanup_total.labels(result="noop").inc()

    logger.info(
        "missing_file_instance_cleanup_completed",
        repository_id=repository_id,
        deleted_instances=deleted_instances,
        deleted_contents=deleted_contents,
        cutoff=cutoff.isoformat(),
        batch_size=batch_size,
        duration_seconds=duration,
    )

    return {
        "status": "success",
        "repository_id": repository_id,
        "deleted_instances": deleted_instances,
        "deleted_contents": deleted_contents,
        "cutoff": cutoff.isoformat(),
        "batch_size": batch_size,
        "duration_seconds": duration,
    }

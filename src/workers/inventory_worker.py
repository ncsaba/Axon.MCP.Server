"""Discovery inventory queue tasks and metadata-gate decisions."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.config.settings import get_settings
from src.config.enums import FileLifecycleStateEnum
from src.database.models import FileInstance as File, Repository
from src.database.session import AsyncSessionLocal
from src.repository_sources import get_repository_source_registry
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    metadata_gate_files_total,
    streaming_stage_batch_size,
    streaming_stage_batches_total,
    streaming_stage_db_duration_seconds,
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
    streaming_stage_lag_seconds,
)
from src.workers.celery_app import celery_app
from src.workers.file_worker import create_or_update_file
from src.workers.utils import _calculate_content_hash, _run_with_engine_cleanup

try:
    import redis.asyncio as redis
except ImportError:  # pragma: no cover - optional dependency in some environments
    redis = None

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="src.workers.inventory_worker.process_discovery_batch",
    max_retries=3,
)
def process_discovery_batch(self, payload: dict) -> dict:
    """Consume one discovery batch and apply metadata-gate decisions."""
    try:
        return asyncio.run(_run_with_engine_cleanup(_process_discovery_batch_async(payload)))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "discovery_batch_processing_failed",
            error=str(exc),
            run_id=payload.get("run_id"),
            batch_seq=payload.get("batch_seq"),
        )
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


async def _process_discovery_batch_async(payload: dict[str, Any]) -> dict[str, Any]:
    _validate_payload(payload)
    settings = get_settings()
    idempotency_key = str(payload["idempotency_key"])
    observed_lag_seconds = _payload_lag_seconds(payload.get("observed_at"))
    if observed_lag_seconds is not None:
        streaming_stage_lag_seconds.labels(stage="metadata_gate").observe(observed_lag_seconds)

    acquired = await _claim_batch_idempotency(
        idempotency_key=idempotency_key,
        ttl_seconds=settings.metadata_gate_idempotency_ttl_seconds,
    )
    if not acquired:
        streaming_stage_batches_total.labels(stage="metadata_gate", status="duplicate").inc()
        logger.info(
            "discovery_batch_duplicate_skipped",
            run_id=payload["run_id"],
            batch_seq=payload["batch_seq"],
            idempotency_key=idempotency_key,
        )
        return {
            "status": "duplicate_skipped",
            "repository_id": payload["repository_id"],
            "run_id": payload["run_id"],
            "batch_seq": payload["batch_seq"],
            "idempotency_key": idempotency_key,
        }

    if not settings.metadata_gate_enabled:
        streaming_stage_batches_total.labels(stage="metadata_gate", status="disabled").inc()
        logger.info(
            "metadata_gate_disabled_batch_accepted",
            repository_id=payload["repository_id"],
            run_id=payload["run_id"],
            batch_seq=payload["batch_seq"],
            files_count=len(payload["files"]),
        )
        return {
            "status": "accepted_metadata_gate_disabled",
            "repository_id": payload["repository_id"],
            "run_id": payload["run_id"],
            "batch_seq": payload["batch_seq"],
            "files_count": len(payload["files"]),
            "idempotency_key": idempotency_key,
        }

    return await _run_metadata_gate(
        payload,
        hash_fallback_enabled=settings.metadata_gate_hash_fallback_enabled,
        inline_parse_enabled=settings.metadata_gate_inline_parse_enabled,
    )


async def _run_metadata_gate(
    payload: dict[str, Any],
    hash_fallback_enabled: bool,
    inline_parse_enabled: bool,
) -> dict[str, Any]:
    stage_started = time.perf_counter()
    repository_id = int(payload["repository_id"])
    current_run_id = int(payload["run_id"])
    batch_files: list[dict[str, Any]] = payload["files"]
    batch_size = len(batch_files)
    streaming_stage_batch_size.labels(stage="metadata_gate").observe(batch_size)
    streaming_stage_items_total.labels(
        stage="metadata_gate",
        item_type="files",
        result="received",
    ).inc(batch_size)

    decision_counts = {
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "unchanged_hash": 0,
        "missing_on_disk": 0,
    }
    parse_file_ids: list[int] = []

    async with AsyncSessionLocal() as session:
        repo = await session.get(Repository, repository_id)
        if repo is None:
            raise ValueError(f"Repository not found for metadata gate: {repository_id}")

        repo_path = get_repository_source_registry().resolve_repository_path(repo)

        rel_paths = [str(item["rel_path"]) for item in batch_files]
        db_started = time.perf_counter()
        existing_records = await session.execute(
            select(File).where(File.repository_id == repository_id, File.path.in_(rel_paths))
        )
        streaming_stage_db_duration_seconds.labels(
            stage="metadata_gate",
            operation="select_existing_files",
        ).observe(time.perf_counter() - db_started)
        existing_by_path = {record.path: record for record in existing_records.scalars().all()}

        for item in batch_files:
            rel_path = str(item["rel_path"])
            file_path = repo_path / rel_path

            if not file_path.exists() or not file_path.is_file():
                decision_counts["missing_on_disk"] += 1
                continue

            existing = existing_by_path.get(rel_path)
            payload_size = int(item["size_bytes"])
            payload_mtime_ns = _safe_int(item.get("mtime_ns"))

            if existing is None:
                file_record = await create_or_update_file(
                    session,
                    repository_id,
                    file_path,
                    repo_path,
                    run_id=current_run_id,
                )
                parse_file_ids.append(file_record.id)
                decision_counts["new"] += 1
                continue

            if _metadata_matches(existing, payload_size, payload_mtime_ns):
                existing.last_seen_run_id = current_run_id
                existing.last_seen_at = datetime.now(UTC)
                existing.lifecycle_state = FileLifecycleStateEnum.ACTIVE
                existing.missing_since = None
                decision_counts["unchanged"] += 1
                continue

            hash_matched = False
            if hash_fallback_enabled and (existing.content_hash or "").strip():
                current_hash = await asyncio.to_thread(_read_content_hash, file_path)
                if current_hash and current_hash == existing.content_hash:
                    existing.size_bytes = payload_size
                    existing.last_modified = _mtime_ns_to_utc(payload_mtime_ns)
                    existing.last_seen_run_id = current_run_id
                    existing.last_seen_at = datetime.now(UTC)
                    existing.lifecycle_state = FileLifecycleStateEnum.ACTIVE
                    existing.missing_since = None
                    decision_counts["unchanged_hash"] += 1
                    hash_matched = True

            if hash_matched:
                continue

            file_record = await create_or_update_file(
                session,
                repository_id,
                file_path,
                repo_path,
                run_id=current_run_id,
            )
            parse_file_ids.append(file_record.id)
            decision_counts["changed"] += 1

        db_started = time.perf_counter()
        await session.commit()
        streaming_stage_db_duration_seconds.labels(
            stage="metadata_gate",
            operation="commit",
        ).observe(time.perf_counter() - db_started)

    for decision, count in decision_counts.items():
        if count:
            metadata_gate_files_total.labels(decision=decision).inc(count)
            streaming_stage_items_total.labels(
                stage="metadata_gate",
                item_type="files",
                result=decision,
            ).inc(count)

    parse_processed = 0
    parse_task_ids: list[str] = []
    changed_content_ids: list[int] = []
    if inline_parse_enabled:
        from src.workers.file_worker import _parse_file_async

        for file_id in parse_file_ids:
            parse_result = await _parse_file_async(file_id)
            if isinstance(parse_result, dict):
                changed_content_ids.extend(
                    int(content_id)
                    for content_id in (parse_result.get("changed_content_ids") or [])
                    if content_id is not None
                )
            parse_processed += 1
    else:
        parse_task_ids = _enqueue_parse_tasks(parse_file_ids)

    logger.info(
        "metadata_gate_batch_processed",
        repository_id=repository_id,
        run_id=payload["run_id"],
        batch_seq=payload["batch_seq"],
        files_total=len(batch_files),
        parse_enqueued=len(parse_file_ids),
        parse_processed=parse_processed,
        parse_mode="inline" if inline_parse_enabled else "queued",
        parse_task_ids_count=len(parse_task_ids),
        changed_content_ids_count=len(changed_content_ids),
        decisions=decision_counts,
    )
    streaming_stage_batches_total.labels(stage="metadata_gate", status="processed").inc()
    streaming_stage_duration_seconds.labels(stage="metadata_gate", mode="streaming").observe(
        time.perf_counter() - stage_started
    )
    if parse_file_ids:
        streaming_stage_items_total.labels(
            stage="metadata_gate",
            item_type="files",
            result="parse_enqueued",
        ).inc(len(parse_file_ids))
    return {
        "status": "processed",
        "repository_id": repository_id,
        "run_id": payload["run_id"],
        "batch_seq": payload["batch_seq"],
        "files_total": len(batch_files),
        "parse_enqueued": len(parse_file_ids),
        "parse_processed": parse_processed,
        "parse_mode": "inline" if inline_parse_enabled else "queued",
        "parse_task_ids": parse_task_ids,
        "parse_file_ids": parse_file_ids,
        "changed_content_ids": sorted(set(changed_content_ids)),
        "decisions": decision_counts,
        "idempotency_key": payload["idempotency_key"],
    }


def _validate_payload(payload: dict[str, Any]) -> None:
    required_fields = {
        "repository_id",
        "run_id",
        "batch_seq",
        "observed_at",
        "files",
        "idempotency_key",
    }
    missing_fields = sorted(required_fields.difference(payload.keys()))
    if missing_fields:
        raise ValueError(f"Invalid discovery batch payload, missing: {', '.join(missing_fields)}")
    if not isinstance(payload.get("files"), list):
        raise ValueError("Invalid discovery batch payload: files must be a list")


async def _claim_batch_idempotency(idempotency_key: str, ttl_seconds: int) -> bool:
    if redis is None:
        return True

    settings = get_settings()
    client = None
    try:
        client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        redis_key = f"inventory_batch:{idempotency_key}"
        claimed = await client.set(redis_key, "1", nx=True, ex=max(1, ttl_seconds))
        return bool(claimed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("inventory_idempotency_claim_failed", key=idempotency_key, error=str(exc))
        return True
    finally:
        if client is not None:
            await client.close()


def _metadata_matches(existing: File, size_bytes: int, mtime_ns: int | None) -> bool:
    existing_mtime_ns = _datetime_to_ns(existing.last_modified)
    if mtime_ns is None or existing_mtime_ns is None:
        return False
    return int(existing.size_bytes or 0) == int(size_bytes) and existing_mtime_ns == mtime_ns


def _read_content_hash(file_path: Path) -> str:
    try:
        content = file_path.read_text(errors="ignore")
    except Exception as exc:  # noqa: BLE001
        logger.warning("metadata_gate_file_read_failed", file_path=str(file_path), error=str(exc))
        return ""
    return _calculate_content_hash(content)


def _datetime_to_ns(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.astimezone(UTC).timestamp() * 1_000_000_000)


def _mtime_ns_to_utc(mtime_ns: int | None) -> datetime | None:
    if mtime_ns is None:
        return None
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=UTC)


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_lag_seconds(observed_at: Any) -> float | None:
    if not observed_at:
        return None
    try:
        value = datetime.fromisoformat(str(observed_at))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - value.astimezone(UTC)).total_seconds())


def _enqueue_parse_tasks(file_ids: list[int]) -> list[str]:
    settings = get_settings()
    chunk_size = max(1, int(settings.metadata_gate_parse_enqueue_chunk_size))
    task_ids: list[str] = []

    for start in range(0, len(file_ids), chunk_size):
        chunk = file_ids[start : start + chunk_size]
        for file_id in chunk:
            async_result = celery_app.send_task(
                "src.workers.tasks.parse_file_task",
                kwargs={"file_id": file_id},
            )
            task_ids.append(str(async_result.id))

    return task_ids

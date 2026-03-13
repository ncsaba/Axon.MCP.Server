"""Discovery inventory queue tasks."""

from __future__ import annotations

from src.utils.logging_config import get_logger
from src.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="src.workers.inventory_worker.process_discovery_batch",
    max_retries=3,
)
def process_discovery_batch(self, payload: dict) -> dict:
    """Validate and acknowledge one discovery batch payload."""
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

    logger.info(
        "discovery_batch_received",
        repository_id=payload["repository_id"],
        run_id=payload["run_id"],
        batch_seq=payload["batch_seq"],
        files_count=len(payload["files"]),
        idempotency_key=payload["idempotency_key"],
    )
    return {
        "status": "accepted",
        "repository_id": payload["repository_id"],
        "run_id": payload["run_id"],
        "batch_seq": payload["batch_seq"],
        "files_count": len(payload["files"]),
    }

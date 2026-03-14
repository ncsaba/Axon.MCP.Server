from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.workers.inventory_worker import (
    _enqueue_parse_tasks,
    _metadata_matches,
    _payload_lag_seconds,
    _process_discovery_batch_async,
    _safe_int,
    _validate_payload,
)


def test_validate_payload_rejects_missing_fields() -> None:
    with pytest.raises(ValueError):
        _validate_payload({"repository_id": 1})


def test_metadata_matches_uses_size_and_mtime_ns() -> None:
    file_record = MagicMock()
    file_record.size_bytes = 123
    file_record.last_modified = datetime.fromtimestamp(1_700_000_000, tz=UTC)

    mtime_ns = int(file_record.last_modified.timestamp() * 1_000_000_000)
    assert _metadata_matches(file_record, 123, mtime_ns) is True
    assert _metadata_matches(file_record, 999, mtime_ns) is False
    assert _metadata_matches(file_record, 123, None) is False


def test_safe_int_handles_invalid_values() -> None:
    assert _safe_int("12") == 12
    assert _safe_int(None) is None
    assert _safe_int("abc") is None


def test_payload_lag_seconds_handles_invalid_values() -> None:
    assert _payload_lag_seconds(None) is None
    assert _payload_lag_seconds("not-a-date") is None


@pytest.mark.asyncio
async def test_process_discovery_batch_disabled_returns_accepted() -> None:
    payload = {
        "repository_id": 1,
        "run_id": "run-1",
        "batch_seq": 1,
        "observed_at": "2026-03-13T00:00:00+00:00",
        "files": [
            {"rel_path": "src/a.py", "size_bytes": 10, "mtime_ns": 1, "kind": "file"},
        ],
        "idempotency_key": "run-1:1",
    }

    settings = MagicMock(
        metadata_gate_enabled=False,
        metadata_gate_hash_fallback_enabled=True,
        metadata_gate_idempotency_ttl_seconds=60,
        metadata_gate_inline_parse_enabled=True,
    )

    lag_metric = MagicMock()
    batch_metric = MagicMock()

    with patch("src.workers.inventory_worker.get_settings", return_value=settings), patch(
        "src.workers.inventory_worker._claim_batch_idempotency",
        return_value=True,
    ), patch(
        "src.workers.inventory_worker.streaming_stage_lag_seconds.labels",
        return_value=lag_metric,
    ), patch(
        "src.workers.inventory_worker.streaming_stage_batches_total.labels",
        return_value=batch_metric,
    ):
        result = await _process_discovery_batch_async(payload)

    assert result["status"] == "accepted_metadata_gate_disabled"
    assert result["files_count"] == 1
    lag_metric.observe.assert_called_once()
    batch_metric.inc.assert_called_once()


def test_enqueue_parse_tasks_respects_chunk_size() -> None:
    settings = MagicMock(metadata_gate_parse_enqueue_chunk_size=2)

    send_result = MagicMock()
    send_result.id = "task-id"

    with patch("src.workers.inventory_worker.get_settings", return_value=settings), patch(
        "src.workers.inventory_worker.celery_app.send_task",
        return_value=send_result,
    ) as send_task:
        task_ids = _enqueue_parse_tasks([1, 2, 3, 4, 5])

    assert send_task.call_count == 5
    assert task_ids == ["task-id", "task-id", "task-id", "task-id", "task-id"]

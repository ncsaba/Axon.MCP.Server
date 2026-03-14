from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workers.pipeline.context import PipelineContext
from src.workers.pipeline.steps.discovery_step import DiscoveryStep


@pytest.mark.asyncio
async def test_discovery_step_emits_inventory_batches(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('a')", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("print('b')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")

    session = AsyncMock()
    repository = MagicMock()
    repository.status = None
    repository.total_files = 0

    ctx = PipelineContext(repository_id=123, session=session)
    ctx.repo_path = tmp_path
    ctx.repository = repository

    publisher = AsyncMock()
    settings = MagicMock(
        parse_max_file_size_mb=10,
        inventory_batch_size=2,
        inventory_max_inflight_batches=2,
        inventory_emit_enabled=True,
        metadata_gate_enabled=False,
    )

    with patch(
        "src.workers.pipeline.steps.discovery_step.RedisLogPublisher",
        return_value=publisher,
    ), patch(
        "src.workers.pipeline.steps.discovery_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.discovery_step.celery_app.send_task",
        return_value=MagicMock(id="task-1"),
    ) as send_task:
        step = DiscoveryStep()
        await step.execute(ctx)

    assert len(ctx.files) == 3
    assert repository.total_files == 3
    assert session.commit.await_count == 1
    assert send_task.call_count == 2

    first_payload = send_task.call_args_list[0].kwargs["kwargs"]["payload"]
    second_payload = send_task.call_args_list[1].kwargs["kwargs"]["payload"]
    assert first_payload["repository_id"] == 123
    assert first_payload["batch_seq"] == 1
    assert first_payload["idempotency_key"].endswith(":1")
    assert second_payload["batch_seq"] == 2
    assert second_payload["idempotency_key"].endswith(":2")
    assert len(first_payload["files"]) == 2
    assert len(second_payload["files"]) == 1
    assert ctx.metadata["parse_task_ids"] == []
    assert ctx.metadata["parse_enqueued_total"] == 0


@pytest.mark.asyncio
async def test_discovery_step_processes_batches_inline_when_metadata_gate_enabled(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('a')", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("print('b')", encoding="utf-8")

    session = AsyncMock()
    repository = MagicMock()
    repository.status = None
    repository.total_files = 0

    ctx = PipelineContext(repository_id=456, session=session)
    ctx.repo_path = tmp_path
    ctx.repository = repository

    publisher = AsyncMock()
    settings = MagicMock(
        parse_max_file_size_mb=10,
        inventory_batch_size=1,
        inventory_max_inflight_batches=2,
        inventory_emit_enabled=True,
        metadata_gate_enabled=True,
    )
    process_result = {
        "parse_enqueued": 1,
        "parse_processed": 0,
        "parse_task_ids": ["task-a"],
        "parse_file_ids": [42],
        "changed_chunk_ids": [1001, 1002],
    }

    with patch(
        "src.workers.pipeline.steps.discovery_step.RedisLogPublisher",
        return_value=publisher,
    ), patch(
        "src.workers.pipeline.steps.discovery_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.discovery_step.celery_app.send_task",
        return_value=MagicMock(id="task-1"),
    ) as send_task, patch(
        "src.workers.inventory_worker._process_discovery_batch_async",
        new_callable=AsyncMock,
        return_value=process_result,
    ) as process_batch:
        step = DiscoveryStep()
        await step.execute(ctx)

    assert process_batch.await_count == 2
    assert send_task.call_count == 0
    assert ctx.metadata["parse_enqueued_total"] == 2
    assert ctx.metadata["parse_processed_total"] == 0
    assert ctx.metadata["parse_file_ids"] == [42]
    assert ctx.metadata["changed_chunk_ids"] == [1001, 1002]
    assert ctx.metadata["parse_task_ids"] == ["task-a"]

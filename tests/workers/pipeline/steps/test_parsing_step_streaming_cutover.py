from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workers.pipeline.context import PipelineContext
from src.workers.pipeline.steps.parsing_step import ParsingStep


@pytest.mark.asyncio
async def test_parsing_step_skips_when_metadata_gate_enabled(tmp_path):
    (tmp_path / "a.py").write_text("print('a')", encoding="utf-8")

    ctx = PipelineContext(repository_id=1, session=AsyncMock())
    ctx.repo_path = tmp_path
    ctx.files = [tmp_path / "a.py"]

    settings = MagicMock(
        metadata_gate_enabled=True,
        inventory_emit_enabled=True,
        metadata_gate_inline_parse_enabled=True,
    )

    with patch(
        "src.workers.pipeline.steps.parsing_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.parse_file_async",
        new_callable=AsyncMock,
    ) as parse_file_async:
        step = ParsingStep()
        await step.execute(ctx)

    parse_file_async.assert_not_awaited()
    assert ctx.timings["parsing"] == 0.0


@pytest.mark.asyncio
async def test_parsing_step_waits_for_streaming_parse_tasks(tmp_path):
    ctx = PipelineContext(repository_id=1, session=AsyncMock())
    ctx.repo_path = tmp_path
    ctx.files = []
    ctx.metadata["parse_task_ids"] = ["task-1", "task-2"]
    ctx.metadata["parse_enqueued_total"] = 2

    settings = MagicMock(
        metadata_gate_enabled=True,
        inventory_emit_enabled=True,
        metadata_gate_inline_parse_enabled=False,
        parse_task_wait_timeout_seconds=10,
        parse_task_wait_poll_seconds=0.01,
    )

    success_result = MagicMock()
    success_result.state = "SUCCESS"
    success_result.result = {"status": "success", "chunk_ids": [11, 22]}
    duration_metric = MagicMock()
    lag_metric = MagicMock()
    batch_metric = MagicMock()
    queue_metric = MagicMock()
    items_metric = MagicMock()

    with patch(
        "src.workers.pipeline.steps.parsing_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.AsyncResult",
        return_value=success_result,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.streaming_stage_duration_seconds.labels",
        return_value=duration_metric,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.streaming_stage_lag_seconds.labels",
        return_value=lag_metric,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.streaming_stage_batch_size.labels",
        return_value=batch_metric,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.streaming_stage_queue_depth.labels",
        return_value=queue_metric,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.streaming_stage_items_total.labels",
        return_value=items_metric,
    ):
        step = ParsingStep()
        await step.execute(ctx)

    assert ctx.files_processed == 2
    assert ctx.metadata["changed_chunk_ids"] == [11, 22]
    assert "parsing" in ctx.timings
    duration_metric.observe.assert_called_once()
    lag_metric.observe.assert_called_once()
    batch_metric.observe.assert_called_once_with(2)
    assert queue_metric.set.call_count >= 1
    assert items_metric.inc.call_count >= 1


@pytest.mark.asyncio
async def test_parsing_step_treats_skipped_unsupported_tasks_as_completed(tmp_path):
    ctx = PipelineContext(repository_id=1, session=AsyncMock())
    ctx.repo_path = tmp_path
    ctx.files = []
    ctx.metadata["parse_task_ids"] = ["task-1", "task-2"]
    ctx.metadata["parse_enqueued_total"] = 2

    settings = MagicMock(
        metadata_gate_enabled=True,
        inventory_emit_enabled=True,
        metadata_gate_inline_parse_enabled=False,
        parse_task_wait_timeout_seconds=10,
        parse_task_wait_poll_seconds=0.01,
    )

    results_by_id = {
        "task-1": MagicMock(state="SUCCESS", result={"status": "skipped_unsupported", "chunk_ids": []}),
        "task-2": MagicMock(state="SUCCESS", result={"status": "success", "chunk_ids": [33]}),
    }

    def async_result_factory(task_id):
        return results_by_id[task_id]

    with patch(
        "src.workers.pipeline.steps.parsing_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.AsyncResult",
        side_effect=async_result_factory,
    ):
        step = ParsingStep()
        await step.execute(ctx)

    assert ctx.files_processed == 2
    assert ctx.metadata["changed_chunk_ids"] == [33]


@pytest.mark.asyncio
async def test_parsing_step_sets_files_processed_for_unchanged_rerun(tmp_path):
    """When no parse tasks exist (unchanged rerun), files_processed should be set to total discovered files."""
    ctx = PipelineContext(repository_id=1, session=AsyncMock())
    ctx.repo_path = tmp_path
    ctx.files = [tmp_path / "a.py", tmp_path / "b.py", tmp_path / "c.py"]
    ctx.metadata["parse_task_ids"] = []  # No parse tasks - all files unchanged
    ctx.metadata["parse_enqueued_total"] = 0

    settings = MagicMock(
        metadata_gate_enabled=True,
        inventory_emit_enabled=True,
        metadata_gate_inline_parse_enabled=False,
        parse_task_wait_timeout_seconds=10,
        parse_task_wait_poll_seconds=0.01,
    )

    duration_metric = MagicMock()
    batch_metric = MagicMock()

    with patch(
        "src.workers.pipeline.steps.parsing_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.streaming_stage_duration_seconds.labels",
        return_value=duration_metric,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.streaming_stage_batch_size.labels",
        return_value=batch_metric,
    ):
        step = ParsingStep()
        await step.execute(ctx)

    # Key assertion: files_processed should be set to total discovered files
    assert ctx.files_processed == 3
    assert ctx.metadata["changed_chunk_ids"] == []
    assert "parsing" in ctx.timings
    duration_metric.observe.assert_called_once()
    batch_metric.observe.assert_called_once_with(0)

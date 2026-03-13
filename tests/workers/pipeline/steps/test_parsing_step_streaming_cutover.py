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

    with patch(
        "src.workers.pipeline.steps.parsing_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.parsing_step.AsyncResult",
        return_value=success_result,
    ):
        step = ParsingStep()
        await step.execute(ctx)

    assert ctx.files_processed == 2
    assert "parsing" in ctx.timings

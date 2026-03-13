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

    settings = MagicMock(metadata_gate_enabled=True, inventory_emit_enabled=True)

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

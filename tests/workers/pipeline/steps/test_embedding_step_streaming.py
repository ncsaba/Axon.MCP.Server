from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.enums import RepositoryStatusEnum
from src.workers.pipeline.context import PipelineContext
from src.workers.pipeline.steps.embedding_step import EmbeddingGenerationStep


@pytest.mark.asyncio
async def test_embedding_step_streaming_generates_only_for_changed_chunks() -> None:
    session = AsyncMock()
    repo = MagicMock()
    repo.status = RepositoryStatusEnum.PARSING
    session.execute.return_value = MagicMock(scalar_one=MagicMock(return_value=repo))

    ctx = PipelineContext(repository_id=123, session=session)
    ctx.metadata["changed_chunk_ids"] = [7, 9, 9]

    settings = MagicMock(metadata_gate_enabled=True, inventory_emit_enabled=True)
    publisher = AsyncMock()
    embedding_result = {
        "status": "success",
        "embeddings_generated": 2,
        "embeddings_generated_new": 1,
        "embeddings_reused": 1,
        "chunks_skipped_existing": 0,
        "chunk_count": 2,
    }
    batch_metric = MagicMock()
    queue_metric = MagicMock()
    duration_metric = MagicMock()
    db_metric = MagicMock()
    items_metric = MagicMock()

    with patch(
        "src.workers.pipeline.steps.embedding_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.embedding_step.RedisLogPublisher",
        return_value=publisher,
    ), patch(
        "src.workers.pipeline.steps.embedding_step._generate_embeddings_async",
        new_callable=AsyncMock,
        return_value=embedding_result,
    ) as generate_async, patch(
        "src.workers.pipeline.steps.embedding_step._generate_repository_embeddings",
        new_callable=AsyncMock,
    ) as generate_repo, patch(
        "src.workers.pipeline.steps.embedding_step.streaming_stage_batch_size.labels",
        return_value=batch_metric,
    ), patch(
        "src.workers.pipeline.steps.embedding_step.streaming_stage_queue_depth.labels",
        return_value=queue_metric,
    ), patch(
        "src.workers.pipeline.steps.embedding_step.streaming_stage_duration_seconds.labels",
        return_value=duration_metric,
    ), patch(
        "src.workers.pipeline.steps.embedding_step.streaming_stage_db_duration_seconds.labels",
        return_value=db_metric,
    ), patch(
        "src.workers.pipeline.steps.embedding_step.streaming_stage_items_total.labels",
        return_value=items_metric,
    ):
        step = EmbeddingGenerationStep()
        await step.execute(ctx)

    generate_async.assert_awaited_once_with([7, 9])
    generate_repo.assert_not_awaited()
    assert ctx.metadata["embeddings_generated"] == 2
    assert ctx.metadata["embedding_generation_result"]["embeddings_reused"] == 1
    batch_metric.observe.assert_called_once_with(2)
    assert queue_metric.set.call_count >= 2
    duration_metric.observe.assert_called_once()
    assert db_metric.observe.call_count >= 2
    assert items_metric.inc.call_count >= 2


@pytest.mark.asyncio
async def test_embedding_step_streaming_skips_when_no_changed_chunks() -> None:
    session = AsyncMock()
    repo = MagicMock()
    repo.status = RepositoryStatusEnum.PARSING
    session.execute.return_value = MagicMock(scalar_one=MagicMock(return_value=repo))

    ctx = PipelineContext(repository_id=456, session=session)
    ctx.metadata["changed_chunk_ids"] = []

    settings = MagicMock(metadata_gate_enabled=True, inventory_emit_enabled=True)
    publisher = AsyncMock()
    queue_metric = MagicMock()

    with patch(
        "src.workers.pipeline.steps.embedding_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.pipeline.steps.embedding_step.RedisLogPublisher",
        return_value=publisher,
    ), patch(
        "src.workers.pipeline.steps.embedding_step._generate_embeddings_async",
        new_callable=AsyncMock,
    ) as generate_async, patch(
        "src.workers.pipeline.steps.embedding_step._generate_repository_embeddings",
        new_callable=AsyncMock,
    ) as generate_repo, patch(
        "src.workers.pipeline.steps.embedding_step.streaming_stage_queue_depth.labels",
        return_value=queue_metric,
    ):
        step = EmbeddingGenerationStep()
        await step.execute(ctx)

    generate_async.assert_not_awaited()
    generate_repo.assert_not_awaited()
    assert ctx.metadata["embeddings_generated"] == 0
    assert ctx.metadata["embedding_generation_result"]["chunk_count"] == 0
    assert queue_metric.set.call_count >= 2

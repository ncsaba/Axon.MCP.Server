from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.embeddings.generator import EmbeddingResult
from src.workers.embedding_worker import _generate_embeddings_async, _plan_embedding_generation


def test_plan_embedding_generation_skips_existing_and_reuses_hash() -> None:
    chunk1 = MagicMock(id=1, content="a", content_hash="h1")
    chunk2 = MagicMock(id=2, content="b", content_hash="h2")
    chunk3 = MagicMock(id=3, content="c", content_hash="h3")

    reusable_by_hash = {
        "h2": EmbeddingResult(
            chunk_id=0,
            vector=[0.1, 0.2],
            model_name="m",
            model_version="1.0",
            dimension=2,
        )
    }

    chunk_data, reused_results, skipped_existing = _plan_embedding_generation(
        chunks=[chunk1, chunk2, chunk3],
        existing_chunk_ids={1},
        reusable_by_hash=reusable_by_hash,
        model_name="m",
        model_version="1.0",
    )

    assert skipped_existing == 1
    assert chunk_data == [{"id": 3, "content": "c"}]
    assert len(reused_results) == 1
    assert reused_results[0].chunk_id == 2
    assert reused_results[0].vector == [0.1, 0.2]


@pytest.mark.asyncio
async def test_generate_embeddings_async_generates_only_for_unmatched_chunks() -> None:
    chunk1 = MagicMock(id=1, content="a", content_hash="h1")
    chunk2 = MagicMock(id=2, content="b", content_hash="h2")
    chunk3 = MagicMock(id=3, content="c", content_hash="h3")

    first_result = MagicMock()
    first_scalars = MagicMock()
    first_scalars.all.return_value = [chunk1, chunk2, chunk3]
    first_result.scalars.return_value = first_scalars

    second_result = MagicMock()
    second_scalars = MagicMock()
    second_scalars.all.return_value = [1]
    second_result.scalars.return_value = second_scalars

    third_result = MagicMock()
    third_result.all.return_value = [("h2", [0.3, 0.4], 2)]

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[first_result, second_result, third_result])
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    session_ctx.__aexit__.return_value = False

    generator = MagicMock()
    generator.model_name = "model-x"
    generator.model_version = "1.0"
    generator.generate_embeddings = AsyncMock(
        return_value=[
            EmbeddingResult(
                chunk_id=3,
                vector=[0.5, 0.6],
                model_name="model-x",
                model_version="1.0",
                dimension=2,
            )
        ]
    )

    vector_store = AsyncMock()
    vector_store.store_embeddings = AsyncMock(return_value=2)

    with patch("src.workers.embedding_worker.AsyncSessionLocal", return_value=session_ctx), patch(
        "src.workers.embedding_worker.EmbeddingGenerator",
        return_value=generator,
    ), patch("src.workers.embedding_worker.PgVectorStore", return_value=vector_store):
        result = await _generate_embeddings_async([1, 2, 3])

    generator.generate_embeddings.assert_awaited_once_with([{"id": 3, "content": "c"}])
    vector_store.store_embeddings.assert_awaited_once()
    stored_results = vector_store.store_embeddings.await_args[0][0]
    assert len(stored_results) == 2
    assert result["status"] == "success"
    assert result["embeddings_generated_new"] == 1
    assert result["embeddings_reused"] == 1
    assert result["chunks_skipped_existing"] == 1

import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.config.enums import LanguageEnum, RepositoryStatusEnum
from src.database.models import Chunk, Embedding, File, FileContent, Repository
from src.embeddings.generator import EmbeddingResult
from src.workers.file_worker import DEFAULT_PARSER_FINGERPRINT
from src.workers.pipeline.context import PipelineContext
from src.workers.pipeline.steps.embedding_step import EmbeddingGenerationStep


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_streaming_embedding_step_skips_unchanged_rerun(async_session) -> None:
    content = "def hello():\n    return 'world'\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    repo = Repository(
        gitlab_project_id=999,
        name="embedding-streaming-rerun",
        path_with_namespace=f"integration/embedding-streaming-rerun-{uuid.uuid4().hex[:10]}",
        url="https://example.com/integration/embedding-streaming-rerun.git",
        clone_url="https://example.com/integration/embedding-streaming-rerun.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()
    repo_id = int(repo.id)

    file_content = FileContent(
        content_hash="embedding-streaming-content",
        language=LanguageEnum.PYTHON,
        parser_fingerprint=DEFAULT_PARSER_FINGERPRINT,
        size_bytes=len(content.encode("utf-8")),
        line_count=len(content.splitlines()),
    )
    async_session.add(file_content)
    await async_session.flush()

    file_row = File(
        repository_id=repo_id,
        path="src/main.py",
        language=LanguageEnum.PYTHON,
        size_bytes=100,
        line_count=3,
        current_content_id=file_content.id,
        content_hash=file_content.content_hash,
    )
    async_session.add(file_row)
    await async_session.flush()

    chunk = Chunk(
        file_id=file_row.id,
        file_content_id=file_row.current_content_id,
        content=content,
        content_type="signature_with_docs",
        token_count=5,
        start_line=1,
        end_line=2,
        content_hash=content_hash,
    )
    async_session.add(chunk)
    await async_session.flush()
    chunk_id = int(chunk.id)

    generator = MagicMock()
    generator.model_name = "test-model"
    generator.model_version = "1.0"
    generator.generate_embeddings = AsyncMock(
        return_value=[
            EmbeddingResult(
                chunk_id=chunk_id,
                vector=[0.1, 0.2, 0.3],
                model_name="test-model",
                model_version="1.0",
                dimension=3,
            )
        ]
    )
    settings = MagicMock(metadata_gate_enabled=True, inventory_emit_enabled=True)

    ctx = PipelineContext(repository_id=repo_id, session=async_session)
    ctx.metadata["changed_chunk_ids"] = [chunk_id]

    with patch(
        "src.workers.pipeline.steps.embedding_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.embedding_worker.EmbeddingGenerator",
        return_value=generator,
    ):
        step = EmbeddingGenerationStep()
        await step.execute(ctx)

    assert ctx.metadata["embeddings_generated"] == 1
    assert generator.generate_embeddings.await_count == 1

    ctx = PipelineContext(repository_id=repo_id, session=async_session)
    ctx.metadata["changed_chunk_ids"] = []

    with patch(
        "src.workers.pipeline.steps.embedding_step.get_settings",
        return_value=settings,
    ), patch(
        "src.workers.embedding_worker.EmbeddingGenerator",
        return_value=generator,
    ):
        step = EmbeddingGenerationStep()
        await step.execute(ctx)

    assert ctx.metadata["embeddings_generated"] == 0
    assert generator.generate_embeddings.await_count == 1

    result = await async_session.execute(
        select(Embedding).join(Chunk).join(File).where(File.repository_id == repo_id)
    )
    embeddings = result.scalars().all()
    assert len(embeddings) == 1

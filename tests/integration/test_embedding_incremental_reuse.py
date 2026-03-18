import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.config.enums import LanguageEnum, RepositoryStatusEnum
from src.database.models import Chunk, Embedding, File, FileContent, Repository
from src.embeddings.generator import EmbeddingResult
from src.workers.file_worker import DEFAULT_PARSER_FINGERPRINT
from src.workers.embedding_worker import _generate_embeddings_async


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_embedding_reused_for_same_chunk_hash(async_session):
    repo = Repository(
        gitlab_project_id=999,
        name="embedding-reuse-repo",
        path_with_namespace=f"integration/embedding-reuse-repo-{uuid.uuid4().hex[:10]}",
        url="https://example.com/integration/embedding-reuse-repo.git",
        clone_url="https://example.com/integration/embedding-reuse-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    content = "def hello():\n    return 'world'\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    async_session.add(repo)
    await async_session.flush()

    file_content = FileContent(
        content_hash="embedding-reuse-content",
        language=LanguageEnum.PYTHON,
        parser_fingerprint=DEFAULT_PARSER_FINGERPRINT,
        size_bytes=len(content.encode("utf-8")),
        line_count=len(content.splitlines()),
    )
    async_session.add(file_content)
    await async_session.flush()

    file_row = File(
        repository_id=repo.id,
        path="src/main.py",
        language=LanguageEnum.PYTHON,
        size_bytes=100,
        line_count=3,
        current_content_id=file_content.id,
        content_hash=file_content.content_hash,
    )
    async_session.add(file_row)
    await async_session.flush()

    chunk1 = Chunk(
        file_id=file_row.id,
        file_content_id=file_row.current_content_id,
        content=content,
        content_type="signature_with_docs",
        token_count=5,
        start_line=1,
        end_line=2,
        content_hash=content_hash,
    )
    async_session.add(chunk1)
    await async_session.flush()
    chunk1_id = int(chunk1.id)
    await async_session.commit()

    generator_instance = MagicMock()
    generator_instance.model_name = "test-model"
    generator_instance.model_version = "1.0"
    generator_instance.generate_embeddings = AsyncMock(
        return_value=[
            EmbeddingResult(
                chunk_id=chunk1_id,
                vector=[0.1, 0.2, 0.3],
                model_name="test-model",
                model_version="1.0",
                dimension=3,
            )
        ]
    )

    with patch("src.workers.embedding_worker.EmbeddingGenerator", return_value=generator_instance):
        first_result = await _generate_embeddings_async([chunk1_id])

    assert first_result["embeddings_generated_new"] == 1
    assert first_result["embeddings_reused"] == 0
    assert generator_instance.generate_embeddings.await_count == 1

    chunk2 = Chunk(
        file_id=file_row.id,
        file_content_id=file_row.current_content_id,
        content=content,
        content_type="signature_with_docs",
        token_count=5,
        start_line=1,
        end_line=2,
        content_hash=content_hash,
    )
    async_session.add(chunk2)
    await async_session.flush()
    chunk2_id = int(chunk2.id)
    await async_session.commit()

    generator_instance.generate_embeddings.reset_mock()
    with patch("src.workers.embedding_worker.EmbeddingGenerator", return_value=generator_instance):
        second_result = await _generate_embeddings_async([chunk2_id])

    assert second_result["embeddings_generated_new"] == 0
    assert second_result["embeddings_reused"] == 1
    assert second_result["chunks_skipped_existing"] == 0
    generator_instance.generate_embeddings.assert_not_awaited()

    result = await async_session.execute(
        select(Embedding).where(Embedding.chunk_id.in_([chunk1_id, chunk2_id]))
    )
    embeddings = result.scalars().all()
    assert len(embeddings) == 2
    assert embeddings[0].model_name == "test-model"
    assert embeddings[1].model_name == "test-model"

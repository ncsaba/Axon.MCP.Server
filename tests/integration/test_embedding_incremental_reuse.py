import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select

from src.config.enums import LanguageEnum, RepositoryStatusEnum
from src.database.models import Chunk, Embedding, File, Repository
from src.database.session import AsyncSessionLocal
from src.embeddings.generator import EmbeddingResult
from src.workers.embedding_worker import _generate_embeddings_async


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_embedding_reused_for_same_chunk_hash():
    repo_path = f"integration/embedding-reuse-repo-{uuid.uuid4().hex[:10]}"

    # Ensure no leftover data if a previous run was interrupted.
    async with AsyncSessionLocal() as cleanup_session:
        await cleanup_session.execute(
            delete(Repository).where(Repository.path_with_namespace == repo_path)
        )
        await cleanup_session.commit()

    repo = Repository(
        gitlab_project_id=999,
        name="embedding-reuse-repo",
        path_with_namespace=repo_path,
        url="https://example.com/integration/embedding-reuse-repo.git",
        clone_url="https://example.com/integration/embedding-reuse-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    content = "def hello():\n    return 'world'\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    try:
        async with AsyncSessionLocal() as setup_session:
            setup_session.add(repo)
            await setup_session.flush()

            file_row = File(
                repository_id=repo.id,
                path="src/main.py",
                language=LanguageEnum.PYTHON,
                size_bytes=100,
                line_count=3,
            )
            setup_session.add(file_row)
            await setup_session.flush()

            chunk1 = Chunk(
                file_id=file_row.id,
                content=content,
                content_type="signature_with_docs",
                token_count=5,
                start_line=1,
                end_line=2,
                content_hash=content_hash,
            )
            setup_session.add(chunk1)
            await setup_session.flush()
            chunk1_id = chunk1.id

            await setup_session.commit()

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

        async with AsyncSessionLocal() as setup_session:
            file_result = await setup_session.execute(
                select(File).where(File.repository_id == repo.id, File.path == "src/main.py")
            )
            file_row = file_result.scalar_one()

            chunk2 = Chunk(
                file_id=file_row.id,
                content=content,
                content_type="signature_with_docs",
                token_count=5,
                start_line=1,
                end_line=2,
                content_hash=content_hash,
            )
            setup_session.add(chunk2)
            await setup_session.flush()
            chunk2_id = chunk2.id
            await setup_session.commit()

        # Second run: same hash, different chunk row -> must reuse and skip model call.
        generator_instance.generate_embeddings.reset_mock()
        with patch("src.workers.embedding_worker.EmbeddingGenerator", return_value=generator_instance):
            second_result = await _generate_embeddings_async([chunk2_id])

        assert second_result["embeddings_generated_new"] == 0
        assert second_result["embeddings_reused"] == 1
        assert second_result["chunks_skipped_existing"] == 0
        generator_instance.generate_embeddings.assert_not_awaited()

        async with AsyncSessionLocal() as verify_session:
            result = await verify_session.execute(
                select(Embedding).where(Embedding.chunk_id.in_([chunk1_id, chunk2_id]))
            )
            embeddings = result.scalars().all()
            assert len(embeddings) == 2
            assert embeddings[0].model_name == "test-model"
            assert embeddings[1].model_name == "test-model"
    finally:
        async with AsyncSessionLocal() as cleanup_session:
            await cleanup_session.execute(
                delete(Repository).where(Repository.path_with_namespace == repo_path)
            )
            await cleanup_session.commit()

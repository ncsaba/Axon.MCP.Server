import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select

from src.config.enums import LanguageEnum, RepositoryStatusEnum
from src.database.models import Chunk, Embedding, File, Repository
from src.database.session import AsyncSessionLocal
from src.embeddings.generator import EmbeddingResult
from src.workers.pipeline.context import PipelineContext
from src.workers.pipeline.steps.embedding_step import EmbeddingGenerationStep


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_streaming_embedding_step_skips_unchanged_rerun() -> None:
    repo_path = f"integration/embedding-streaming-rerun-{uuid.uuid4().hex[:10]}"
    content = "def hello():\n    return 'world'\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    async with AsyncSessionLocal() as cleanup_session:
        await cleanup_session.execute(
            delete(Repository).where(Repository.path_with_namespace == repo_path)
        )
        await cleanup_session.commit()

    try:
        async with AsyncSessionLocal() as setup_session:
            repo = Repository(
                gitlab_project_id=999,
                name="embedding-streaming-rerun",
                path_with_namespace=repo_path,
                url="https://example.com/integration/embedding-streaming-rerun.git",
                clone_url="https://example.com/integration/embedding-streaming-rerun.git",
                default_branch="main",
                status=RepositoryStatusEnum.PENDING,
            )
            setup_session.add(repo)
            await setup_session.flush()
            repo_id = int(repo.id)

            file_row = File(
                repository_id=repo_id,
                path="src/main.py",
                language=LanguageEnum.PYTHON,
                size_bytes=100,
                line_count=3,
            )
            setup_session.add(file_row)
            await setup_session.flush()

            chunk = Chunk(
                file_id=file_row.id,
                content=content,
                content_type="signature_with_docs",
                token_count=5,
                start_line=1,
                end_line=2,
                content_hash=content_hash,
            )
            setup_session.add(chunk)
            await setup_session.flush()
            chunk_id = int(chunk.id)

            await setup_session.commit()

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

        # First run with changed chunks -> one generation call.
        async with AsyncSessionLocal() as first_session:
            ctx = PipelineContext(repository_id=repo_id, session=first_session)
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

        # Second run unchanged -> no changed chunks, no new generation call.
        async with AsyncSessionLocal() as second_session:
            ctx = PipelineContext(repository_id=repo_id, session=second_session)
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

        async with AsyncSessionLocal() as verify_session:
            result = await verify_session.execute(
                select(Embedding).join(Chunk).join(File).where(File.repository_id == repo_id)
            )
            embeddings = result.scalars().all()
            assert len(embeddings) == 1
    finally:
        async with AsyncSessionLocal() as cleanup_session:
            await cleanup_session.execute(
                delete(Repository).where(Repository.path_with_namespace == repo_path)
            )
            await cleanup_session.commit()

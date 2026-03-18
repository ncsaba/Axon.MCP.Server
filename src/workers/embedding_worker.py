"""
Celery tasks for embedding generation.
"""

from celery import shared_task
from typing import List
import asyncio
import traceback
from sqlalchemy import select

from src.workers.celery_app import celery_app
from src.workers.utils import _run_with_engine_cleanup
from src.database.session import AsyncSessionLocal
from src.database.models import Chunk, Embedding, File
from src.database.query_helpers import active_file_filter
from src.embeddings.generator import EmbeddingGenerator, EmbeddingResult
from src.vector_store.pgvector_store import PgVectorStore
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


async def _generate_repository_embeddings(
    session,
    repository_id: int,
    task=None
) -> int:
    """
    Generate embeddings for all chunks in repository.
    
    Args:
        session: Database session
        repository_id: Repository ID
        task: Celery task (for progress updates)
        
    Returns:
        Number of embeddings generated
    """
    # Get all chunks for repository
    result = await session.execute(
        select(Chunk)
        .join(File)
        .where(File.repository_id == repository_id, active_file_filter())
    )
    chunks = result.scalars().all()
    
    if not chunks:
        logger.warning(
            "no_chunks_found_for_embeddings",
            repository_id=repository_id
        )
        return 0
    
    logger.info(
        "generating_embeddings",
        repository_id=repository_id,
        chunk_count=len(chunks)
    )
    
    # Prepare chunks for embedding
    chunk_data = [
        {'id': chunk.id, 'content': chunk.content}
        for chunk in chunks
    ]
    
    # Generate embeddings in batches
    generator = EmbeddingGenerator()
    embedding_results = await generator.generate_embeddings(chunk_data)
    
    # Store in vector store
    vector_store = PgVectorStore(session)
    stored = await vector_store.store_embeddings(embedding_results)
    
    # Update progress
    if task:
        task.update_state(
            state='PROGRESS',
            meta={
                'status': 'embedding',
                'embeddings_generated': stored,
                'phase': 'embedding_generation'
            }
        )
    
    logger.info(
        "repository_embeddings_generated",
        repository_id=repository_id,
        count=stored
    )
    
    return stored


@celery_app.task(
    bind=True,
    name="src.workers.tasks.generate_embeddings_task",
    max_retries=3
)
def generate_embeddings_task(self, chunk_ids: List[int]):
    """
    Generate embeddings for specific chunks.
    
    Args:
        chunk_ids: List of chunk IDs
        
    Returns:
        dict: Generation result
    """
    logger.info(
        "generate_embeddings_task_started",
        chunk_count=len(chunk_ids),
        task_id=self.request.id
    )
    
    try:
        result = asyncio.run(_run_with_engine_cleanup(_generate_embeddings_async(chunk_ids)))
        return result
    except Exception as e:
        error_msg = f"Failed to generate embeddings: {str(e)}"
        logger.error(
            "generate_embeddings_task_failed",
            chunk_count=len(chunk_ids),
            error=error_msg,
            traceback=traceback.format_exc()
        )
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


async def _generate_embeddings_async(chunk_ids: List[int]):
    """Async implementation of embedding generation."""
    
    # Use AsyncSessionLocal directly for manual transaction management
    async with AsyncSessionLocal() as session:
        try:
            # Get chunks
            result = await session.execute(
                select(Chunk).where(Chunk.id.in_(chunk_ids))
            )
            chunks = result.scalars().all()
            
            if not chunks:
                error_msg = f"Failed to generate embeddings: No chunks found for IDs {chunk_ids}"
                logger.warning("no_chunks_found", chunk_ids=chunk_ids, error=error_msg)
                return {"status": "error", "error": error_msg}
            
            generator = EmbeddingGenerator()
            existing_chunk_ids, reusable_by_hash = await _lookup_embedding_reuse_candidates(
                session=session,
                chunks=chunks,
                model_name=generator.model_name,
                model_version=generator.model_version,
            )

            chunk_data, reused_results, skipped_existing = _plan_embedding_generation(
                chunks=chunks,
                existing_chunk_ids=existing_chunk_ids,
                reusable_by_hash=reusable_by_hash,
                model_name=generator.model_name,
                model_version=generator.model_version,
            )

            generated_results: List[EmbeddingResult] = []
            if chunk_data:
                generated_results = await generator.generate_embeddings(chunk_data)

            # Store embeddings
            vector_store = PgVectorStore(session)
            stored = await vector_store.store_embeddings(reused_results + generated_results)
            
            await session.commit()
            
            logger.info(
                "embeddings_generated_successfully",
                chunk_count=len(chunk_ids),
                embeddings_stored=stored,
                embeddings_generated=len(generated_results),
                embeddings_reused=len(reused_results),
                chunks_skipped_existing=skipped_existing,
            )
            
            return {
                "status": "success",
                "embeddings_generated": stored,
                "embeddings_generated_new": len(generated_results),
                "embeddings_reused": len(reused_results),
                "chunks_skipped_existing": skipped_existing,
                "chunk_count": len(chunks),
            }
            
        except Exception as e:
            error_msg = f"Failed to generate embeddings: {str(e)}"
            logger.error(
                "embedding_generation_failed",
                chunk_count=len(chunk_ids),
                error=error_msg,
                traceback=traceback.format_exc()
            )
            await session.rollback()
            raise


async def _lookup_embedding_reuse_candidates(
    session,
    chunks: List[Chunk],
    model_name: str,
    model_version: str,
) -> tuple[set[int], dict[str, EmbeddingResult]]:
    chunk_ids = [chunk.id for chunk in chunks]
    hash_values = sorted({chunk.content_hash for chunk in chunks if chunk.content_hash})

    existing_chunk_ids: set[int] = set()
    reusable_by_hash: dict[str, EmbeddingResult] = {}

    if chunk_ids:
        existing_result = await session.execute(
            select(Embedding.chunk_id).where(
                Embedding.chunk_id.in_(chunk_ids),
                Embedding.model_name == model_name,
                Embedding.model_version == model_version,
            )
        )
        existing_chunk_ids = {int(chunk_id) for chunk_id in existing_result.scalars().all()}

    if hash_values:
        reuse_result = await session.execute(
            select(
                Chunk.content_hash,
                Embedding.vector,
                Embedding.dimension,
            )
            .join(Embedding, Embedding.chunk_id == Chunk.id)
            .where(
                Chunk.content_hash.in_(hash_values),
                Embedding.model_name == model_name,
                Embedding.model_version == model_version,
            )
        )
        for content_hash, vector, dimension in reuse_result.all():
            if content_hash and content_hash not in reusable_by_hash:
                reusable_by_hash[content_hash] = EmbeddingResult(
                    chunk_id=0,
                    vector=vector,
                    model_name=model_name,
                    model_version=model_version,
                    dimension=int(dimension),
                )

    return existing_chunk_ids, reusable_by_hash


def _plan_embedding_generation(
    chunks: List[Chunk],
    existing_chunk_ids: set[int],
    reusable_by_hash: dict[str, EmbeddingResult],
    model_name: str,
    model_version: str,
) -> tuple[list[dict], list[EmbeddingResult], int]:
    chunk_data: list[dict] = []
    reused_results: list[EmbeddingResult] = []
    skipped_existing = 0

    for chunk in chunks:
        if int(chunk.id) in existing_chunk_ids:
            skipped_existing += 1
            continue

        if chunk.content_hash and chunk.content_hash in reusable_by_hash:
            reusable = reusable_by_hash[chunk.content_hash]
            reused_results.append(
                EmbeddingResult(
                    chunk_id=int(chunk.id),
                    vector=reusable.vector,
                    model_name=model_name,
                    model_version=model_version,
                    dimension=int(reusable.dimension),
                )
            )
            continue

        chunk_data.append({"id": int(chunk.id), "content": chunk.content})

    return chunk_data, reused_results, skipped_existing

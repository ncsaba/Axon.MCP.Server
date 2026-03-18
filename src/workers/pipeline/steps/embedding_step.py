import time
from celery import current_task
from sqlalchemy import select
from src.database.models import Chunk, Repository
from src.config.enums import RepositoryStatusEnum
from src.config.settings import get_settings
from src.utils.metrics import (
    streaming_stage_batch_size,
    streaming_stage_db_duration_seconds,
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
    streaming_stage_queue_depth,
)
from src.workers.embedding_worker import _generate_repository_embeddings, _generate_embeddings_async
from src.utils.redis_logger import RedisLogPublisher
from src.utils.logging_config import get_logger
from ..step import PipelineStep
from ..context import PipelineContext

logger = get_logger(__name__)

class EmbeddingGenerationStep(PipelineStep):
    """
    Step 11: Generate embeddings.
    """
    
    async def execute(self, ctx: PipelineContext) -> None:
        publisher = RedisLogPublisher()
        start_time = time.time()
        settings = get_settings()
        
        # Re-fetch repo object and update status
        db_started = time.perf_counter()
        result = await ctx.session.execute(select(Repository).where(Repository.id == ctx.repository_id))
        streaming_stage_db_duration_seconds.labels(stage="embedding", operation="select_repository").observe(
            time.perf_counter() - db_started
        )
        repo = result.scalar_one()
        ctx.repository = repo
        
        repo.status = RepositoryStatusEnum.EMBEDDING
        db_started = time.perf_counter()
        await ctx.session.commit()
        streaming_stage_db_duration_seconds.labels(stage="embedding", operation="commit").observe(
            time.perf_counter() - db_started
        )
    
        logger.info(
            "embedding_generation_started",
            repository_id=ctx.repository_id
        )
        await publisher.publish_log(ctx.repository_id, "Generating embeddings...")
    
        embeddings_generated = 0
        if settings.metadata_gate_enabled and settings.inventory_emit_enabled:
            changed_content_ids = sorted(
                {
                    int(content_id)
                    for content_id in (ctx.metadata.get("changed_content_ids") or [])
                    if content_id is not None
                }
            )
            changed_chunk_ids = sorted(
                {
                    int(chunk_id)
                    for chunk_id in (ctx.metadata.get("changed_chunk_ids") or [])
                    if chunk_id is not None
                }
            )
            if changed_content_ids:
                chunk_id_result = await ctx.session.execute(
                    select(Chunk.id).where(Chunk.file_content_id.in_(changed_content_ids))
                )
                changed_chunk_ids = [int(chunk_id) for chunk_id in chunk_id_result.scalars().all()]

            streaming_stage_batch_size.labels(stage="embedding").observe(len(changed_chunk_ids))
            streaming_stage_queue_depth.labels(stage="embedding").set(len(changed_chunk_ids))
            if changed_chunk_ids:
                embedding_result = await _generate_embeddings_async(changed_chunk_ids)
                embeddings_generated = int(embedding_result.get("embeddings_generated", 0) or 0)
                ctx.metadata["embedding_generation_result"] = embedding_result
                streaming_stage_items_total.labels(
                    stage="embedding",
                    item_type="chunks",
                    result="requested",
                ).inc(len(changed_chunk_ids))
                streaming_stage_items_total.labels(
                    stage="embedding",
                    item_type="embeddings",
                    result="generated",
                ).inc(embeddings_generated)
                reused_count = int(embedding_result.get("embeddings_reused", 0) or 0)
                if reused_count:
                    streaming_stage_items_total.labels(
                        stage="embedding",
                        item_type="embeddings",
                        result="reused",
                    ).inc(reused_count)
            else:
                logger.info(
                    "embedding_generation_skipped_no_changed_chunks",
                    repository_id=ctx.repository_id,
                )
                ctx.metadata["embedding_generation_result"] = {
                    "status": "success",
                    "embeddings_generated": 0,
                    "embeddings_generated_new": 0,
                    "embeddings_reused": 0,
                    "chunks_skipped_existing": 0,
                    "chunk_count": 0,
                }
        else:
            # Pass the current Celery task if available, or None
            task = current_task
            embeddings_generated = await _generate_repository_embeddings(
                ctx.session,
                ctx.repository_id,
                task
            )
            streaming_stage_items_total.labels(
                stage="embedding",
                item_type="embeddings",
                result="generated_full_repository",
            ).inc(embeddings_generated)
        streaming_stage_queue_depth.labels(stage="embedding").set(0)
        
        ctx.metrics.embeddings_generated = embeddings_generated
        ctx.metadata['embeddings_generated'] = embeddings_generated
    
        logger.info(
            "embedding_generation_completed",
            repository_id=ctx.repository_id,
            embeddings_generated=embeddings_generated
        )
        await publisher.publish_log(ctx.repository_id, f"Embedding generation completed. Generated {embeddings_generated} embeddings.", details={"embeddings_generated": embeddings_generated})

        duration = time.time() - start_time
        mode = "streaming" if settings.metadata_gate_enabled and settings.inventory_emit_enabled else "batch"
        streaming_stage_duration_seconds.labels(stage="embedding", mode=mode).observe(duration)
        ctx.timings['embedding_generation'] = duration

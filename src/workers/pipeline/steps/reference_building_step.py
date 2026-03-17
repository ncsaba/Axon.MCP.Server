import time
from src.extractors.reference_builder import ReferenceBuilder
from src.utils.redis_logger import RedisLogPublisher
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
)
from ..step import PipelineStep
from ..context import PipelineContext

logger = get_logger(__name__)

class ReferenceBuildingStep(PipelineStep):
    """
    Step 4.5: Build reference relationships (now that all symbols exist).
    """
    
    async def execute(self, ctx: PipelineContext) -> None:
        publisher = RedisLogPublisher()
        start_time = time.perf_counter()
        
        logger.info("reference_building_started", repository_id=ctx.repository_id)
        await publisher.publish_log(ctx.repository_id, "Building reference relationships...")

        ref_relations = 0
        try:
            reference_builder = ReferenceBuilder(ctx.session)
            ref_relations = await reference_builder.build_all_references(ctx.repository_id)
            
            ctx.metadata['references_created'] = ref_relations
            
            logger.info(
                "reference_building_completed",
                repository_id=ctx.repository_id,
                references_created=ref_relations
            )
            await publisher.publish_log(
                ctx.repository_id,
                f"Reference building completed. Created {ref_relations} references.",
                details={"references_created": ref_relations}
            )
            
        except Exception as e:
            logger.error(
                "reference_building_failed",
                repository_id=ctx.repository_id,
                error=str(e)
            )
            await publisher.publish_log(ctx.repository_id, f"Reference building failed: {str(e)}", level="ERROR")
            # Continue - don't fail entire sync

        duration = time.perf_counter() - start_time
        ctx.timings['reference_building'] = duration
        
        # Emit streaming stage metrics
        streaming_stage_duration_seconds.labels(stage="reference_building", mode="batch").observe(duration)
        streaming_stage_items_total.labels(
            stage="reference_building",
            item_type="references",
            result="created"
        ).inc(ref_relations)

import time
from src.config.settings import get_settings
from src.utils.redis_logger import RedisLogPublisher
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
)
from ..step import PipelineStep
from ..context import PipelineContext

logger = get_logger(__name__)

class DependencyExtractionStep(PipelineStep):
    """
    Step 7.5: Extract dependencies (if enabled).
    """
    
    async def execute(self, ctx: PipelineContext) -> None:
        if not get_settings().extract_dependencies:
            return

        if not ctx.repo_path:
             logger.warning("skipping_dependency_extraction_no_path")
             return

        publisher = RedisLogPublisher()
        start_time = time.perf_counter()
        
        logger.info(
            "dependency_extraction_started",
            repository_id=ctx.repository_id
        )
        await publisher.publish_log(ctx.repository_id, "Extracting package dependencies...")
        
        dependencies_found = 0
        try:
            # Import strictly locally as in original code
            from src.extractors.dependency_extractor import DependencyExtractor
            
            dep_extractor = DependencyExtractor(ctx.session)
            dependencies_found = await dep_extractor.extract_dependencies(ctx.repository_id, ctx.repo_path)
            
            ctx.metrics.dependencies_found = dependencies_found
            ctx.metadata['dependencies_found'] = dependencies_found
            
            logger.info(
                "dependency_extraction_completed",
                repository_id=ctx.repository_id,
                dependencies_found=dependencies_found
            )
            await publisher.publish_log(
                ctx.repository_id,
                f"Dependency extraction completed. Found {dependencies_found} dependencies.",
                details={"dependencies_found": dependencies_found}
            )
            
        except Exception as e:
            logger.error(
                "dependency_extraction_failed",
                repository_id=ctx.repository_id,
                error=str(e)
            )
            await publisher.publish_log(ctx.repository_id, f"Dependency extraction failed: {str(e)}", level="ERROR")
            # Continue even if dependency extraction fails

        duration = time.perf_counter() - start_time
        ctx.timings['dependency_extraction'] = duration
        
        # Emit streaming stage metrics
        streaming_stage_duration_seconds.labels(stage="dependency_extraction", mode="batch").observe(duration)
        streaming_stage_items_total.labels(
            stage="dependency_extraction",
            item_type="dependencies",
            result="found"
        ).inc(dependencies_found)

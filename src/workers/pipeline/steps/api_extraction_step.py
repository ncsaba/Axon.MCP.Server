import time
from src.config.settings import get_settings
from src.extractors.api_extractor import ApiEndpointExtractor
from src.utils.redis_logger import RedisLogPublisher
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
)
from ..step import PipelineStep
from ..context import PipelineContext

logger = get_logger(__name__)

class ApiExtractionStep(PipelineStep):
    """
    Step 4: Extract API endpoints (if enabled).
    """
    
    async def execute(self, ctx: PipelineContext) -> None:
        if not get_settings().extract_api_endpoints:
            return

        publisher = RedisLogPublisher()
        start_time = time.perf_counter()
        
        logger.info(
            "api_endpoint_extraction_started",
            repository_id=ctx.repository_id
        )
        await publisher.publish_log(ctx.repository_id, "Starting API endpoint extraction...")
        
        api_endpoints_count = 0
        try:
            # Re-fetch objects if needed or assume session is clean
            api_extractor = ApiEndpointExtractor(ctx.session)
            endpoints = await api_extractor.extract_endpoints(ctx.repository_id)
            await api_extractor.save_endpoints(endpoints)
            await ctx.session.commit()  # Commit endpoints to database
            
            api_endpoints_count = len(endpoints)
            ctx.metrics.api_endpoints_count = api_endpoints_count
            ctx.metadata['api_endpoints_count'] = api_endpoints_count
            
            logger.info(
                "api_endpoint_extraction_completed",
                repository_id=ctx.repository_id,
                endpoints_found=api_endpoints_count
            )
            await publisher.publish_log(ctx.repository_id, f"API endpoint extraction completed. Found {api_endpoints_count} endpoints.", details={"endpoints_found": api_endpoints_count})
            
        except Exception as e:
            logger.error(
                "api_endpoint_extraction_failed",
                repository_id=ctx.repository_id,
                error=str(e)
            )
            await publisher.publish_log(ctx.repository_id, f"API endpoint extraction failed: {str(e)}", level="ERROR")
            # Continue even if API extraction fails

        duration = time.perf_counter() - start_time
        ctx.timings['api_extraction'] = duration
        
        # Emit streaming stage metrics
        streaming_stage_duration_seconds.labels(stage="api_extraction", mode="batch").observe(duration)
        streaming_stage_items_total.labels(
            stage="api_extraction",
            item_type="endpoints",
            result="created"
        ).inc(api_endpoints_count)

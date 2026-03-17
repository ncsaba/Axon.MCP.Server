import time
from src.config.settings import get_settings
from src.extractors.call_graph_builder import CallGraphBuilder
from src.utils.redis_logger import RedisLogPublisher
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
)
from ..step import PipelineStep
from ..context import PipelineContext

logger = get_logger(__name__)

class CallGraphStep(PipelineStep):
    """
    Step 7: Build call graph (if enabled).
    
    OPTIMIZATION: In streaming mode, skips processing when no files changed.
    Call relationships are defined by the caller's source code, so unchanged
    files have unchanged call relationships.
    """
    
    async def execute(self, ctx: PipelineContext) -> None:
        if not get_settings().build_call_graph:
            return

        publisher = RedisLogPublisher()
        start_time = time.perf_counter()
        
        # Early-exit optimization: check if any files changed
        parse_file_ids = ctx.metadata.get("parse_file_ids", [])
        settings = get_settings()
        
        if settings.metadata_gate_enabled and not parse_file_ids:
            # No files changed - skip call graph building entirely
            logger.info(
                "call_graph_skipped_no_changes",
                repository_id=ctx.repository_id,
                reason="no_files_changed_in_streaming_mode"
            )
            await publisher.publish_log(
                ctx.repository_id, 
                "Call graph building skipped (no files changed)",
                details={"reason": "unchanged_rerun"}
            )
            
            duration = time.perf_counter() - start_time
            ctx.timings['call_graph'] = duration
            
            # Emit skip metric
            streaming_stage_duration_seconds.labels(stage="call_graph", mode="streaming").observe(duration)
            streaming_stage_items_total.labels(
                stage="call_graph",
                item_type="files",
                result="skipped"
            ).inc(len(ctx.files) if ctx.files else 0)
            return
        
        logger.info(
            "call_graph_building_started",
            repository_id=ctx.repository_id,
            changed_file_count=len(parse_file_ids)
        )
        await publisher.publish_log(ctx.repository_id, "Building call graph...")
        
        call_relationships_created = 0
        try:
            call_graph_builder = CallGraphBuilder(ctx.session)
            # Pass changed file IDs for selective processing
            call_relationships_created = await call_graph_builder.build_call_relationships(
                ctx.repository_id,
                changed_file_ids=parse_file_ids if parse_file_ids else None
            )
            
            ctx.metrics.call_relationships_created = call_relationships_created
            ctx.metadata['call_relationships_created'] = call_relationships_created
            
            logger.info(
                "call_graph_building_completed",
                repository_id=ctx.repository_id,
                relationships_created=call_relationships_created
            )
            await publisher.publish_log(ctx.repository_id, f"Call graph building completed. Created {call_relationships_created} relationships.", details={"relationships_created": call_relationships_created})
            
        except Exception as e:
            logger.error(
                "call_graph_building_failed",
                repository_id=ctx.repository_id,
                error=str(e)
            )
            await publisher.publish_log(ctx.repository_id, f"Call graph building failed: {str(e)}", level="ERROR")
            # Continue even if call graph building fails

        duration = time.perf_counter() - start_time
        ctx.timings['call_graph'] = duration
        
        # Emit streaming stage metrics
        mode = "streaming" if settings.metadata_gate_enabled else "batch"
        streaming_stage_duration_seconds.labels(stage="call_graph", mode=mode).observe(duration)
        streaming_stage_items_total.labels(
            stage="call_graph",
            item_type="call_relationships",
            result="created"
        ).inc(call_relationships_created)

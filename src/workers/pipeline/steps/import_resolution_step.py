import time
from src.config.settings import get_settings
from src.extractors.import_resolver import ImportRelationshipBuilder
from src.utils.redis_logger import RedisLogPublisher
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
)
from ..step import PipelineStep
from ..context import PipelineContext

logger = get_logger(__name__)

class ImportResolutionStep(PipelineStep):
    """
    Step 6: Build import relationships (if enabled).
    
    OPTIMIZATION: In streaming mode, skips processing when no files changed.
    Import resolution has bidirectional dependency:
    - Unchanged file's imports depend on that file's source (unchanged)
    - But resolution depends on target file's exported symbols (may change)
    
    For unchanged reruns (no files changed), skip entirely.
    For partial changes, we still process all files because:
    - An unchanged file may import from a changed file
    - The changed file may have added/removed exported symbols
    - This affects the unchanged file's import resolution
    """
    
    async def execute(self, ctx: PipelineContext) -> None:
        if not get_settings().extract_imports:
            return

        publisher = RedisLogPublisher()
        start_time = time.perf_counter()
        
        # Early-exit optimization: check if any files changed
        parse_file_ids = ctx.metadata.get("parse_file_ids", [])
        settings = get_settings()
        
        if settings.metadata_gate_enabled and not parse_file_ids:
            # No files changed - skip import resolution entirely
            logger.info(
                "import_resolution_skipped_no_changes",
                repository_id=ctx.repository_id,
                reason="no_files_changed_in_streaming_mode"
            )
            await publisher.publish_log(
                ctx.repository_id, 
                "Import resolution skipped (no files changed)",
                details={"reason": "unchanged_rerun"}
            )
            
            duration = time.perf_counter() - start_time
            ctx.timings['import_resolution'] = duration
            
            # Emit skip metric
            streaming_stage_duration_seconds.labels(stage="import_resolution", mode="streaming").observe(duration)
            streaming_stage_items_total.labels(
                stage="import_resolution",
                item_type="files",
                result="skipped"
            ).inc(len(ctx.files) if ctx.files else 0)
            return
        
        logger.info(
            "import_relationship_building_started",
            repository_id=ctx.repository_id,
            changed_file_count=len(parse_file_ids)
        )
        await publisher.publish_log(ctx.repository_id, "Building import relationships...")
        
        import_relationships_created = 0
        try:
            if not ctx.repo_path:
                 raise ValueError("Repo path missing for import resolution")

            import_builder = ImportRelationshipBuilder(ctx.session, ctx.repo_path)
            import_relationships_created = await import_builder.build_import_relationships(ctx.repository_id)
            
            ctx.metrics.import_relationships_created = import_relationships_created
            ctx.metadata['import_relationships_created'] = import_relationships_created
            
            logger.info(
                "import_relationship_building_completed",
                repository_id=ctx.repository_id,
                relationships_created=import_relationships_created
            )
            await publisher.publish_log(ctx.repository_id, f"Import relationship building completed. Created {import_relationships_created} relationships.", details={"relationships_created": import_relationships_created})
            
        except Exception as e:
            logger.error(
                "import_relationship_building_failed",
                repository_id=ctx.repository_id,
                error=str(e)
            )
            await publisher.publish_log(ctx.repository_id, f"Import relationship building failed: {str(e)}", level="ERROR")
            # Continue even if import resolution fails

        duration = time.perf_counter() - start_time
        ctx.timings['import_resolution'] = duration
        
        # Emit streaming stage metrics
        mode = "streaming" if settings.metadata_gate_enabled else "batch"
        streaming_stage_duration_seconds.labels(stage="import_resolution", mode=mode).observe(duration)
        streaming_stage_items_total.labels(
            stage="import_resolution",
            item_type="imports",
            result="created"
        ).inc(import_relationships_created)

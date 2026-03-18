import time
import asyncio
from sqlalchemy import select
from src.database.models import Repository, FileInstance as File
from src.database.query_helpers import active_file_filter
from src.extractors.outgoing_call_extractor import OutgoingCallExtractor
from src.extractors.event_extractor import EventExtractor
from src.utils.redis_logger import RedisLogPublisher
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
)
from ..step import PipelineStep
from ..context import PipelineContext

logger = get_logger(__name__)

class CombinedExtractionStep(PipelineStep):
    """
    Step 9 & 10: Extract outgoing API calls and events.
    Uses keyset pagination for memory efficiency.
    """
    
    async def execute(self, ctx: PipelineContext) -> None:
        publisher = RedisLogPublisher()
        start_time = time.perf_counter()
        
        outgoing_calls_count = 0
        published_events_count = 0
        event_subscriptions_count = 0
        
        logger.info("combined_extraction_started", repository_id=ctx.repository_id)
        await publisher.publish_log(ctx.repository_id, "Extracting events and outgoing calls...")
        
        try:
            outgoing_extractor = OutgoingCallExtractor(ctx.session)
            event_extractor = EventExtractor(ctx.session)
            
            # Fetch files in batches using Keyset Pagination (Seek Method)
            last_file_id = 0
            while True:
                # Fetch next batch
                stmt = (
                    select(File)
                    .where(
                        File.repository_id == ctx.repository_id,
                        File.id > last_file_id,
                        active_file_filter(),
                    )
                    .order_by(File.id)
                    .limit(50)
                )
                batch_result = await ctx.session.execute(stmt)
                file_batch = batch_result.scalars().all()
                
                if not file_batch:
                    break
                    
                for file_obj in file_batch:
                    last_file_id = file_obj.id
                    if not ctx.repo_path:
                         continue # Safe guard
                          
                    file_path = ctx.repo_path / file_obj.path
                    
                    # Read content once
                    if not file_path.exists():
                        continue
                    
                    try:
                        # Use to_thread for file reading to avoid blocking loop
                        content = await asyncio.to_thread(file_path.read_text, encoding='utf-8', errors='ignore')
                        
                        # Extract outgoing calls
                        calls = await outgoing_extractor.extract_from_file(file_obj, ctx.repo_path, content=content)
                        for call in calls:
                            ctx.session.add(call)
                            outgoing_calls_count += 1
                            
                        # Extract events
                        events = await event_extractor.extract_from_file(file_obj, ctx.repo_path, content=content)
                        for event in events['published']:
                            ctx.session.add(event)
                            published_events_count += 1
                        for sub in events['subscribed']:
                            ctx.session.add(sub)
                            event_subscriptions_count += 1
                        
                    except Exception as e:
                        logger.error(
                            f"Failed to process file {file_obj.path} for extraction: {e}",
                            exc_info=True
                        )
                        continue

                # Commit per batch to persist changes and release locks
                await ctx.session.commit()
                
                # Clear session to free memory
                # This prevents OOM by ensuring only the current batch stays in memory
                ctx.session.expunge_all()
                
                logger.debug(
                    "extraction_batch_committed",
                    outgoing_calls=outgoing_calls_count,
                    published_events=published_events_count,
                    subscriptions=event_subscriptions_count,
                    last_processed_id=last_file_id
                )
                
                # Fix 1.2: Refresh repository object after session.expunge_all()
                await ctx.refresh_repository()

            # Update typed metrics
            ctx.metrics.outgoing_calls_count = outgoing_calls_count
            ctx.metrics.published_events_count = published_events_count
            ctx.metrics.event_subscriptions_count = event_subscriptions_count
            
            logger.info(
                "combined_extraction_completed",
                outgoing_calls=outgoing_calls_count,
                published_events=published_events_count,
                event_subscriptions=event_subscriptions_count
            )
            await publisher.publish_log(
                ctx.repository_id,
                f"Extraction completed. Found {outgoing_calls_count} outgoing calls, {published_events_count} published events, {event_subscriptions_count} subscriptions.",
                details={
                    "outgoing_calls": outgoing_calls_count,
                    "published_events": published_events_count,
                    "event_subscriptions": event_subscriptions_count
                }
            )
            
        except Exception as e:
            logger.error("combined_extraction_failed", error=str(e), exc_info=True)
            await publisher.publish_log(ctx.repository_id, f"Extraction failed: {str(e)}", level="ERROR")
            # Rollback to clean up any uncommitted work from the failed batch
            await ctx.session.rollback()
            raise # CRITICAL for this step to ensure no partial incomplete states if batch fails badly

        duration = time.perf_counter() - start_time
        ctx.timings['combined_extraction'] = duration
        
        # Emit streaming stage metrics
        streaming_stage_duration_seconds.labels(stage="combined_extraction", mode="batch").observe(duration)
        streaming_stage_items_total.labels(
            stage="combined_extraction",
            item_type="outgoing_calls",
            result="created"
        ).inc(outgoing_calls_count)
        streaming_stage_items_total.labels(
            stage="combined_extraction",
            item_type="events_published",
            result="created"
        ).inc(published_events_count)
        streaming_stage_items_total.labels(
            stage="combined_extraction",
            item_type="events_subscribed",
            result="created"
        ).inc(event_subscriptions_count)

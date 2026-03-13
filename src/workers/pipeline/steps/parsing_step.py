import asyncio
import time
from celery.result import AsyncResult
from celery.states import READY_STATES
from sqlalchemy import select
from celery import current_task

from src.config.settings import get_settings
from src.utils.redis_logger import RedisLogPublisher
from src.utils.logging_config import get_logger
from src.utils.file_exclusion import FileExclusionRules
from src.workers.file_worker import create_or_update_file
from src.extractors.knowledge_extractor import KnowledgeExtractor
from src.parsers import parse_file_async
from ..step import PipelineStep
from ..context import PipelineContext

logger = get_logger(__name__)

class ParsingStep(PipelineStep):
    """
    Step 3: Parse files and extract knowledge.
    Iterates through discovered files, parses them, and extracts symbols.
    Handles batch committing and memory management.
    """
    
    # Fix 3.1: Strict dependency validation
    requires_fields = ["files", "repo_path"]

    async def execute(self, ctx: PipelineContext) -> None:
        settings = get_settings()
        if settings.metadata_gate_enabled and settings.inventory_emit_enabled:
            if settings.metadata_gate_inline_parse_enabled:
                logger.info(
                    "parsing_step_skipped_streaming_cutover",
                    repository_id=ctx.repository_id,
                    reason="metadata_gate_inline_parse_enabled",
                )
                ctx.timings["parsing"] = 0.0
                return

            await self._wait_for_streaming_parse_tasks(ctx, settings)
            return

        if not ctx.files:
            logger.warning("no_files_to_parse", repository_id=ctx.repository_id)
            return

        publisher = RedisLogPublisher()
        start_time = time.time()
        
        # Instantiate extractor ONCE to reuse helper objects
        extractor = KnowledgeExtractor(ctx.session)
        
        # Re-create exclusion rules if finding them in metadata, otherwise default
        exclusion_rules = ctx.metadata.get('exclusion_rules') or FileExclusionRules()
        if not exclusion_rules and (ctx.repo_path / '.gitignore').exists():
             pass 

        files_processed = 0
        total_chunks_created = 0 # Fix 1.1: Local accumulator to avoid metric inflation
        total_files = len(ctx.files)
        
        for idx, file_path in enumerate(ctx.files):
            try:
                relative_path = str(file_path.relative_to(ctx.repo_path))
            
                # Mark if test or generated (re-check rules)
                is_test = exclusion_rules.is_test_file(relative_path)
                is_generated = exclusion_rules.is_generated_file(relative_path)
            
                # Parse file
                logger.debug(
                    "parsing_file",
                    file_path=str(file_path),
                    progress=f"{idx + 1}/{total_files}",
                    is_test=is_test,
                    is_generated=is_generated
                )
                
                # Use async parser
                parse_result = await parse_file_async(file_path)
            
                # Create or update file record
                file_record = await create_or_update_file(
                    ctx.session,
                    ctx.repository_id,
                    file_path,
                    ctx.repo_path
                )
            
                # Extract knowledge
                extraction_result = await extractor.extract_and_persist(
                    parse_result,
                    file_record.id
                )
                
                # Update metrics
                chunks_count = extraction_result.chunks_created
                total_chunks_created += chunks_count
                ctx.metrics.chunks_created = total_chunks_created
                
                # Explicitly delete heavy objects to free memory immediately
                del parse_result
                del extraction_result
            
                files_processed += 1
                ctx.files_processed = files_processed
            
                # Update progress every 10 files
                if files_processed % 10 == 0:
                     # Check if we are running in a Celery task context
                    if current_task:
                        current_task.update_state(
                            state='PROGRESS',
                            meta={
                                'current': files_processed,
                                'total': total_files,
                                'status': 'parsing',
                                'phase': 'file_parsing'
                            }
                        )
                    
                    logger.info(
                        "parsing_progress",
                        repository_id=ctx.repository_id,
                        files_processed=files_processed,
                        total_files=total_files
                    )
                    await publisher.publish_log(
                        ctx.repository_id,
                        f"Parsed {files_processed}/{total_files} files...", 
                        details={"current": files_processed, "total": total_files}
                    )
            
                # Commit and clear session periodically to prevent memory bloat
                if files_processed % 50 == 0:
                    await ctx.session.commit()
                    # Expunge all objects from session to free memory
                    # This is critical for large repositories to prevent OOM
                    # NOTE: This detaches 'repo' object.
                    ctx.session.expunge_all()
                    
                    # Fix 1.2: Refresh repository object after session.expunge_all()
                    await ctx.refresh_repository()

            except Exception as e:
                error_msg = f"Failed to parse file: {str(e)}"
                logger.error(
                    "file_parsing_failed",
                    file_path=str(file_path),
                    repository_id=ctx.repository_id,
                    error=error_msg
                )
                # Rollback transaction to recover from potential database errors
                await ctx.session.rollback()
                # Continue with next file
                continue
    
        logger.info(
            "repository_parsing_completed",
            repository_id=ctx.repository_id,
            files_processed=files_processed
        )
        await publisher.publish_log(ctx.repository_id, f"Parsing completed. Processed {files_processed} files.", details={"files_processed": files_processed})
        
        ctx.timings['parsing'] = time.time() - start_time

    async def _wait_for_streaming_parse_tasks(self, ctx: PipelineContext, settings) -> None:
        start_time = time.time()
        task_ids = [str(x) for x in (ctx.metadata.get("parse_task_ids") or []) if x]
        if not task_ids:
            logger.info(
                "no_parse_tasks_from_metadata_gate",
                repository_id=ctx.repository_id,
            )
            ctx.timings["parsing"] = time.time() - start_time
            return

        timeout_seconds = max(1, int(settings.parse_task_wait_timeout_seconds))
        poll_seconds = max(0.1, float(settings.parse_task_wait_poll_seconds))
        deadline = time.monotonic() + timeout_seconds

        pending = set(task_ids)
        failed: dict[str, str] = {}

        while pending and time.monotonic() < deadline:
            completed_now: list[str] = []
            for task_id in pending:
                result = AsyncResult(task_id)
                state = str(result.state)
                if state not in READY_STATES:
                    continue

                completed_now.append(task_id)
                if state != "SUCCESS":
                    failed[task_id] = state

            for task_id in completed_now:
                pending.discard(task_id)

            if pending:
                await asyncio.sleep(poll_seconds)

        if pending:
            raise TimeoutError(
                f"Timed out waiting for {len(pending)} parse tasks after {timeout_seconds}s"
            )

        if failed:
            failed_summary = ", ".join(f"{task_id}:{state}" for task_id, state in sorted(failed.items()))
            raise RuntimeError(f"Parse task failures detected: {failed_summary}")

        ctx.files_processed = int(ctx.metadata.get("parse_enqueued_total", len(task_ids)) or len(task_ids))
        logger.info(
            "streaming_parse_tasks_completed",
            repository_id=ctx.repository_id,
            tasks_total=len(task_ids),
            files_processed=ctx.files_processed,
        )
        ctx.timings["parsing"] = time.time() - start_time

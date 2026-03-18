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
from src.utils.metrics import (
    streaming_stage_batch_size,
    streaming_stage_duration_seconds,
    streaming_stage_items_total,
    streaming_stage_lag_seconds,
    streaming_stage_queue_depth,
)
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
        total_symbols_created = 0
        total_files = len(ctx.files)
        streaming_stage_batch_size.labels(stage="parsing").observe(total_files)
        
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
                    ctx.repo_path,
                    run_id=ctx.metadata.get("current_run_id"),
                )
            
                # Extract knowledge
                extraction_result = await extractor.extract_and_persist(
                    parse_result,
                    file_record.id
                )
                
                # Update metrics
                total_symbols_created += extraction_result.symbols_created
                chunks_count = extraction_result.chunks_created
                total_chunks_created += chunks_count
                ctx.metrics.symbols_created = total_symbols_created
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
        duration = time.time() - start_time
        streaming_stage_duration_seconds.labels(stage="parsing", mode="batch").observe(duration)
        streaming_stage_items_total.labels(
            stage="parsing",
            item_type="files",
            result="processed",
        ).inc(files_processed)
        ctx.timings['parsing'] = duration

    async def _wait_for_streaming_parse_tasks(self, ctx: PipelineContext, settings) -> None:
        start_time = time.time()
        task_ids = [str(x) for x in (ctx.metadata.get("parse_task_ids") or []) if x]
        changed_chunk_ids = {
            int(chunk_id)
            for chunk_id in (ctx.metadata.get("changed_chunk_ids") or [])
            if chunk_id is not None
        }
        if not task_ids:
            # No parse tasks means all files were unchanged (metadata gate skip).
            # Set files_processed to total discovered files for accurate job_metadata.
            total_discovered_files = len(ctx.files) if ctx.files else 0
            ctx.files_processed = total_discovered_files
            logger.info(
                "no_parse_tasks_from_metadata_gate",
                repository_id=ctx.repository_id,
                total_discovered_files=total_discovered_files,
            )
            ctx.metadata["changed_chunk_ids"] = sorted(changed_chunk_ids)
            streaming_stage_batch_size.labels(stage="parse_wait").observe(0)
            streaming_stage_duration_seconds.labels(stage="parse_wait", mode="streaming").observe(
                time.time() - start_time
            )
            ctx.timings["parsing"] = time.time() - start_time
            return

        timeout_seconds = max(1, int(settings.parse_task_wait_timeout_seconds))
        poll_seconds = max(0.1, float(settings.parse_task_wait_poll_seconds))
        deadline = time.monotonic() + timeout_seconds
        streaming_stage_batch_size.labels(stage="parse_wait").observe(len(task_ids))

        pending = set(task_ids)
        failed: dict[str, str] = {}
        symbols_created = 0
        chunks_created = 0

        while pending and time.monotonic() < deadline:
            streaming_stage_queue_depth.labels(stage="parse_wait").set(len(pending))
            completed_now: list[str] = []
            for task_id in pending:
                result = AsyncResult(task_id)
                state = str(result.state)
                if state not in READY_STATES:
                    continue

                completed_now.append(task_id)
                if state != "SUCCESS":
                    failed[task_id] = state
                    continue

                payload = result.result
                if isinstance(payload, dict):
                    symbols_created += int(payload.get("symbols_created", 0) or 0)
                    chunks_created += int(payload.get("chunks_created", 0) or 0)
                    for chunk_id in payload.get("chunk_ids", []) or []:
                        if chunk_id is not None:
                            changed_chunk_ids.add(int(chunk_id))
                    if payload.get("status") == "error":
                        failed[task_id] = "APPLICATION_ERROR"
                elif payload is not None:
                    failed[task_id] = "INVALID_RESULT"

            for task_id in completed_now:
                pending.discard(task_id)

            if pending:
                await asyncio.sleep(poll_seconds)

        streaming_stage_queue_depth.labels(stage="parse_wait").set(0)

        if pending:
            raise TimeoutError(
                f"Timed out waiting for {len(pending)} parse tasks after {timeout_seconds}s"
            )

        if failed:
            failed_summary = ", ".join(f"{task_id}:{state}" for task_id, state in sorted(failed.items()))
            raise RuntimeError(f"Parse task failures detected: {failed_summary}")

        ctx.files_processed = int(ctx.metadata.get("parse_enqueued_total", len(task_ids)) or len(task_ids))
        ctx.metrics.symbols_created = symbols_created
        ctx.metrics.chunks_created = chunks_created
        ctx.metadata["changed_chunk_ids"] = sorted(changed_chunk_ids)
        parse_wait_duration = time.time() - start_time
        streaming_stage_duration_seconds.labels(stage="parse_wait", mode="streaming").observe(
            parse_wait_duration
        )
        streaming_stage_lag_seconds.labels(stage="parse_wait").observe(parse_wait_duration)
        streaming_stage_items_total.labels(
            stage="parse_wait",
            item_type="tasks",
            result="completed",
        ).inc(len(task_ids))
        if changed_chunk_ids:
            streaming_stage_items_total.labels(
                stage="parse_wait",
                item_type="chunks",
                result="changed",
            ).inc(len(changed_chunk_ids))
        logger.info(
            "streaming_parse_tasks_completed",
            repository_id=ctx.repository_id,
            tasks_total=len(task_ids),
            files_processed=ctx.files_processed,
            changed_chunk_ids=len(changed_chunk_ids),
        )
        ctx.timings["parsing"] = parse_wait_duration

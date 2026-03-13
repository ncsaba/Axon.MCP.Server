import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from src.config.enums import RepositoryStatusEnum
from src.config.settings import get_settings
from src.utils.file_exclusion import FileExclusionRules
from src.utils.logging_config import get_logger
from src.utils.metrics import (
    directories_enumerated_total,
    files_enumerated_total,
    inventory_batches_emitted_total,
    inventory_emit_latency_ms,
    inventory_queue_lag,
)
from src.utils.redis_logger import RedisLogPublisher
from src.workers.celery_app import celery_app
from src.workers.file_inventory import (
    DEFAULT_DISCOVERY_EXTENSIONS,
    FileMeta,
    ScandirFileInventoryProvider,
)
from ..context import PipelineContext
from ..step import PipelineStep

logger = get_logger(__name__)

INVENTORY_BACKEND = "scandir_fallback"


class DiscoveryStep(PipelineStep):
    """
    Step 2: Discover files in repository and emit streaming inventory batches.
    Populates context.files for current parsing-step compatibility.
    """

    async def execute(self, ctx: PipelineContext) -> None:
        if not ctx.repo_path:
            raise ValueError("Repository path not set in context")

        settings = get_settings()
        publisher = RedisLogPublisher()
        start_time = time.time()
        repo = ctx.repository

        exclusion_rules = self._build_exclusion_rules(ctx.repo_path)
        file_size_limit_bytes = settings.parse_max_file_size_mb * 1024 * 1024
        inventory_provider = ScandirFileInventoryProvider()

        run_id = ctx.metadata.get("inventory_run_id") or ctx.execution_id
        ctx.metadata["inventory_run_id"] = run_id

        batch_size = max(1, settings.inventory_batch_size)
        max_inflight_batches = max(1, settings.inventory_max_inflight_batches)
        emit_enabled = bool(settings.inventory_emit_enabled)

        files: list[Path] = []
        pending_emits: set[asyncio.Task[None]] = set()
        current_batch: list[FileMeta] = []
        batch_seq = 0
        parse_task_ids: set[str] = set()
        parse_totals = {"enqueued": 0, "processed": 0}

        def should_include(rel_path: str, size_bytes: int) -> bool:
            suffix = Path(rel_path).suffix.lower()
            if suffix not in DEFAULT_DISCOVERY_EXTENSIONS:
                return False
            if size_bytes > file_size_limit_bytes:
                logger.warning(
                    "file_too_large_skipped",
                    repository_id=ctx.repository_id,
                    rel_path=rel_path,
                    size_mb=size_bytes / (1024 * 1024),
                )
                return False
            return True

        for file_meta in inventory_provider.stream(
            ctx.repo_path,
            should_include=should_include,
            should_exclude=exclusion_rules.should_exclude,
        ):
            files.append(ctx.repo_path / file_meta.rel_path)
            current_batch.append(file_meta)

            if emit_enabled and len(current_batch) >= batch_size:
                batch_seq += 1
                await self._schedule_batch_emit(
                    repository_id=ctx.repository_id,
                    run_id=run_id,
                    batch_seq=batch_seq,
                    batch_files=current_batch,
                    pending_emits=pending_emits,
                    max_inflight_batches=max_inflight_batches,
                    parse_task_ids=parse_task_ids,
                    parse_totals=parse_totals,
                )
                current_batch = []

        if emit_enabled and current_batch:
            batch_seq += 1
            await self._schedule_batch_emit(
                repository_id=ctx.repository_id,
                run_id=run_id,
                batch_seq=batch_seq,
                batch_files=current_batch,
                pending_emits=pending_emits,
                max_inflight_batches=max_inflight_batches,
                parse_task_ids=parse_task_ids,
                parse_totals=parse_totals,
            )

        if pending_emits:
            done, _ = await asyncio.wait(pending_emits)
            for completed in done:
                result = await completed
                self._collect_parse_fanout(result, parse_task_ids, parse_totals)
            inventory_queue_lag.labels(backend=INVENTORY_BACKEND).set(0)

        files_before = inventory_provider.files_seen
        files_excluded = max(0, files_before - len(files))

        logger.info(
            "files_filtered",
            repository_id=ctx.repository_id,
            total_files=files_before,
            excluded=files_excluded,
            remaining=len(files),
            inventory_batches_emitted=batch_seq,
            inventory_backend=INVENTORY_BACKEND,
        )

        files_enumerated_total.labels(backend=INVENTORY_BACKEND).inc(len(files))
        directories_enumerated_total.labels(backend=INVENTORY_BACKEND).inc(
            inventory_provider.directories_enumerated
        )

        ctx.files = files
        ctx.metadata["exclusion_rules"] = exclusion_rules
        ctx.metadata["inventory_batches_emitted"] = batch_seq
        ctx.metadata["parse_task_ids"] = sorted(parse_task_ids)
        ctx.metadata["parse_enqueued_total"] = parse_totals["enqueued"]
        ctx.metadata["parse_processed_total"] = parse_totals["processed"]

        repo.status = RepositoryStatusEnum.PARSING
        repo.total_files = len(files)
        await ctx.session.commit()

        logger.info(
            "repository_parsing_started",
            repository_id=ctx.repository_id,
            total_files=len(files),
            inventory_batches_emitted=batch_seq,
        )
        await publisher.publish_log(
            ctx.repository_id,
            f"Starting to parse {len(files)} files...",
            details={
                "total_files": len(files),
                "inventory_batches_emitted": batch_seq,
                "inventory_backend": INVENTORY_BACKEND,
                "parse_enqueued_total": parse_totals["enqueued"],
                "parse_processed_total": parse_totals["processed"],
            },
        )

        ctx.timings["discovery"] = time.time() - start_time

    @staticmethod
    def _build_exclusion_rules(repo_path: Path) -> FileExclusionRules:
        exclusion_rules = FileExclusionRules()
        gitignore_path = repo_path / ".gitignore"
        if gitignore_path.exists():
            gitignore_patterns = FileExclusionRules.parse_gitignore(gitignore_path)
            exclusion_rules = FileExclusionRules(custom_exclusions=gitignore_patterns)
        return exclusion_rules

    async def _schedule_batch_emit(
        self,
        repository_id: int,
        run_id: str,
        batch_seq: int,
        batch_files: list[FileMeta],
        pending_emits: set[asyncio.Task[None]],
        max_inflight_batches: int,
        parse_task_ids: set[str],
        parse_totals: dict[str, int],
    ) -> None:
        payload = {
            "repository_id": repository_id,
            "run_id": run_id,
            "batch_seq": batch_seq,
            "observed_at": datetime.now(UTC).isoformat(),
            "files": [self._serialize_file_meta(file_meta) for file_meta in batch_files],
            "idempotency_key": f"{run_id}:{batch_seq}",
        }

        emit_task = asyncio.create_task(self._emit_inventory_batch(payload))
        pending_emits.add(emit_task)
        inventory_queue_lag.labels(backend=INVENTORY_BACKEND).set(len(pending_emits))

        if len(pending_emits) >= max_inflight_batches:
            done, still_pending = await asyncio.wait(
                pending_emits,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for completed in done:
                result = await completed
                self._collect_parse_fanout(result, parse_task_ids, parse_totals)
            pending_emits.clear()
            pending_emits.update(still_pending)
            inventory_queue_lag.labels(backend=INVENTORY_BACKEND).set(len(pending_emits))

        # keep counters visible for caller even before all tasks complete
        parse_totals["enqueued"] = parse_totals.get("enqueued", 0)
        parse_totals["processed"] = parse_totals.get("processed", 0)

    async def _emit_inventory_batch(self, payload: dict) -> dict:
        start = time.perf_counter()
        try:
            if get_settings().metadata_gate_enabled:
                from src.workers.inventory_worker import _process_discovery_batch_async

                result = await _process_discovery_batch_async(payload)
            else:
                await asyncio.to_thread(
                    celery_app.send_task,
                    "src.workers.inventory_worker.process_discovery_batch",
                    kwargs={"payload": payload},
                )
                result = {}
            inventory_batches_emitted_total.labels(
                backend=INVENTORY_BACKEND,
                status="success",
            ).inc()
            return result
        except Exception:
            inventory_batches_emitted_total.labels(
                backend=INVENTORY_BACKEND,
                status="error",
            ).inc()
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            inventory_emit_latency_ms.labels(backend=INVENTORY_BACKEND).observe(elapsed_ms)

    @staticmethod
    def _serialize_file_meta(file_meta: FileMeta) -> dict:
        return {
            "rel_path": file_meta.rel_path,
            "size_bytes": file_meta.size_bytes,
            "mtime_ns": file_meta.mtime_ns,
            "kind": file_meta.kind,
        }

    @staticmethod
    def _collect_parse_fanout(
        result: dict,
        parse_task_ids: set[str],
        parse_totals: dict[str, int],
    ) -> None:
        if not isinstance(result, dict):
            return

        for task_id in result.get("parse_task_ids", []) or []:
            parse_task_ids.add(str(task_id))

        parse_totals["enqueued"] = parse_totals.get("enqueued", 0) + int(
            result.get("parse_enqueued", 0) or 0
        )
        parse_totals["processed"] = parse_totals.get("processed", 0) + int(
            result.get("parse_processed", 0) or 0
        )

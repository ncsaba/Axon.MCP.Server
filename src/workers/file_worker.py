"""
Celery tasks for file processing.
"""

from celery import shared_task
from pathlib import Path
import asyncio
import traceback
from datetime import UTC, datetime
from sqlalchemy import select

from src.workers.celery_app import celery_app
from src.workers.utils import _run_with_engine_cleanup, _calculate_content_hash
from src.gitlab.repository_manager import RepositoryManager
from src.parsers import parse_file
from src.extractors.knowledge_extractor import KnowledgeExtractor
from src.database.session import AsyncSessionLocal
from src.database.models import Repository, File, Chunk
from src.repository_sources import get_repository_source_registry
from src.utils.logging_config import get_logger
from src.utils.async_compat import maybe_await

logger = get_logger(__name__)


def _read_file_stat(file_path: Path) -> tuple[int, datetime | None]:
    """Return `(size_bytes, last_modified_utc)` with resilient fallback."""
    try:
        stat_result = file_path.stat()
        size_bytes = int(stat_result.st_size)
        last_modified = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)
        return size_bytes, last_modified
    except Exception as exc:  # noqa: BLE001
        logger.warning("file_stat_failed", file_path=str(file_path), error=str(exc))
        return 0, None


async def create_or_update_file(
    session,
    repository_id: int,
    file_path: Path,
    repo_path: Path
) -> File:
    """
    Create or update file record.
    
    Args:
        session: Database session
        repository_id: Repository ID
        file_path: Absolute file path
        repo_path: Repository root path
        
    Returns:
        File record
    """
    relative_path = file_path.relative_to(repo_path)
    size_bytes, last_modified = _read_file_stat(file_path)
    
    # Check if file exists
    result = await session.execute(
        select(File).where(
            File.repository_id == repository_id,
            File.path == str(relative_path)
        )
    )
    file_record = result.scalar_one_or_none()
    
    if not file_record:
        # Create new file record
        repo_manager = RepositoryManager()
        language = repo_manager.detect_language(file_path)
        
        try:
            content = file_path.read_text(errors='ignore')
            line_count = len(content.splitlines())
            # Calculate content hash for module summary optimization
            content_hash = _calculate_content_hash(content)
        except Exception as e:
            error_msg = f"Failed to read file: {str(e)}"
            logger.warning(
                "file_read_failed",
                file_path=str(file_path),
                error=error_msg
            )
            line_count = 0
            content_hash = ""
        
        file_record = File(
            repository_id=repository_id,
            path=str(relative_path),
            language=language,
            size_bytes=size_bytes,
            last_modified=last_modified,
            line_count=line_count,
            content_hash=content_hash
        )
        await maybe_await(session.add(file_record))
        await session.flush()
    else:
        # Update existing file record
        file_record.size_bytes = size_bytes
        file_record.last_modified = last_modified
        try:
            content = file_path.read_text(errors='ignore')
            file_record.line_count = len(content.splitlines())
            # Recalculate content hash to detect changes
            new_content_hash = _calculate_content_hash(content)
            # Only update if hash has changed to prevent unnecessary module summary regeneration
            if file_record.content_hash != new_content_hash:
                logger.debug(
                    "file_content_changed",
                    file_path=str(file_path),
                    old_hash=file_record.content_hash,
                    new_hash=new_content_hash
                )
                file_record.content_hash = new_content_hash
        except Exception:
            pass
    
    return file_record


@celery_app.task(bind=True, name="src.workers.tasks.parse_file_task", max_retries=3)
def parse_file_task(self, file_id: int):
    """
    Parse a single file.
    
    Args:
        file_id: File ID in database
        
    Returns:
        dict: Parse result
    """
    logger.info("parse_file_task_started", file_id=file_id, task_id=self.request.id)
    
    try:
        result = asyncio.run(_run_with_engine_cleanup(_parse_file_async(file_id)))
        return result
    except Exception as e:
        error_msg = f"Failed to parse file: {str(e)}"
        logger.error(
            "parse_file_task_failed",
            file_id=file_id,
            error=error_msg,
            traceback=traceback.format_exc()
        )
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


async def _parse_file_async(file_id: int):
    """Async implementation of file parsing."""
    
    # Use AsyncSessionLocal directly for manual transaction management
    async with AsyncSessionLocal() as session:
        try:
            # Get file record
            result = await session.execute(
                select(File).where(File.id == file_id)
            )
            file_record = result.scalar_one_or_none()
            
            if not file_record:
                error_msg = f"Failed to parse file: File with ID {file_id} not found"
                logger.error("file_not_found", file_id=file_id, error=error_msg)
                return {"status": "error", "error": error_msg}
            
            # Get repository to construct full path
            result = await session.execute(
                select(Repository).where(Repository.id == file_record.repository_id)
            )
            repo = result.scalar_one()
            
            repo_path = get_repository_source_registry().resolve_repository_path(repo)
            file_path = repo_path / file_record.path
            
            if not file_path.exists():
                error_msg = f"Failed to parse file: File not found on disk at {file_path}"
                logger.error("file_not_found_on_disk", file_path=str(file_path), error=error_msg)
                return {"status": "error", "error": error_msg}
            
            # Parse file
            parse_result = await asyncio.to_thread(parse_file, file_path)
            
            # Extract knowledge
            extractor = KnowledgeExtractor(session)
            extraction_result = await extractor.extract_and_persist(
                parse_result,
                file_record.id
            )

            chunk_result = await session.execute(
                select(Chunk.id).where(Chunk.file_id == file_id)
            )
            chunk_ids = [int(chunk_id) for chunk_id in chunk_result.scalars().all()]
            
            await session.commit()
            
            logger.info(
                "file_parsed_successfully",
                file_id=file_id,
                symbols_extracted=extraction_result.symbols_created  # ExtractionResult is a dataclass
            )
            
            return {
                "status": "success",
                "file_id": file_id,
                "symbols_created": extraction_result.symbols_created,  # Attribute access
                "chunks_created": extraction_result.chunks_created,  # Attribute access
                "chunk_ids": chunk_ids,
            }
            
        except Exception as e:
            error_msg = f"Failed to parse file: {str(e)}"
            logger.error(
                "file_parsing_failed",
                file_id=file_id,
                error=error_msg,
                traceback=traceback.format_exc()
            )
            await session.rollback()
            raise

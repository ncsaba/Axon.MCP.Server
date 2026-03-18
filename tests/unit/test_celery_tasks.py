"""
Unit tests for Celery tasks and worker utilities.
Focuses on individual worker component logic.
Pipeline orchestration is tested in tests/integration/test_worker_pipeline.py.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock, Mock
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.database.models import Repository, FileInstance as File, Chunk, Job
from src.config.embedding_contract import FIXED_EMBEDDING_DIMENSION
from src.config.enums import (
    RepositoryStatusEnum,
    JobStatusEnum,
    LanguageEnum,
    SymbolKindEnum
)
from src.parsers.base_parser import ParseResult, ParsedSymbol
from src.workers.file_worker import create_or_update_file as _create_or_update_file
from src.workers.embedding_worker import _generate_repository_embeddings
from src.workers.utils import _count_symbols
from src.workers.job_monitor import JobMonitor


@pytest.fixture
def mock_repository():
    """Mock repository."""
    repo = MagicMock(spec=Repository)
    repo.id = 1
    repo.gitlab_project_id = 12345
    repo.name = "test-repo"
    repo.path_with_namespace = "test/repo"
    repo.url = "https://github.com/test/repo.git"
    repo.default_branch = "main"
    repo.status = RepositoryStatusEnum.PENDING
    repo.total_files = 0
    return repo

@pytest.fixture
def mock_file():
    """Mock file."""
    file = MagicMock(spec=File)
    file.id = 1
    file.repository_id = 1
    file.path = "src/test.py"
    file.language = LanguageEnum.PYTHON
    file.size_bytes = 1024
    file.line_count = 50
    return file

@pytest.fixture
def mock_chunk():
    """Mock chunk."""
    chunk = MagicMock(spec=Chunk)
    chunk.id = 1
    chunk.content = "def test_function():\n    return True"
    chunk.token_count = 10
    return chunk


@pytest.mark.asyncio
async def test_create_or_update_file_new_file(mock_repository):
    """Test creating a new file record."""
    session = AsyncMock()
    
    # Mock no existing file
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result_mock)
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    
    test_file = MagicMock(spec=Path)
    test_file.read_text.return_value = "test content\nline 2"
    test_file.relative_to.return_value = Path("test_temp_file.txt")
    test_file.stat.return_value.st_size = 100
    test_file.stat.return_value.st_mtime = 1710000000.0
    
    repo_path = Path(".")
    
    with patch('src.workers.file_worker.RepositoryManager') as mock_repo_manager:
        mock_manager = MagicMock()
        mock_manager.detect_language.return_value = LanguageEnum.PYTHON
        mock_repo_manager.return_value = mock_manager
        
        file_record = await _create_or_update_file(
            session,
            1,
            test_file,
            repo_path
        )
        
        # Verify file was added to session
        assert session.add.called
        assert session.flush.called
        assert file_record.size_bytes == 100
        assert file_record.last_modified == datetime.fromtimestamp(
            test_file.stat.return_value.st_mtime,
            tz=UTC,
        )


@pytest.mark.asyncio
async def test_create_or_update_file_existing_file(mock_file):
    """Test updating an existing file record."""
    session = AsyncMock()
    
    # Mock existing file
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = mock_file
    session.execute = AsyncMock(return_value=result_mock)
    
    test_file = MagicMock(spec=Path)
    test_file.read_text.return_value = "updated content\nline 2\nline 3"
    test_file.relative_to.return_value = Path("test_temp_file.txt")
    test_file.stat.return_value.st_size = 150
    test_file.stat.return_value.st_mtime = 1710001234.0
    
    repo_path = Path(".")
    
    file_record = await _create_or_update_file(
        session,
        1,
        test_file,
        repo_path
    )
    
    # Verify file was updated (using object Identity mock_file)
    assert file_record is mock_file
    assert file_record.size_bytes == 150
    assert file_record.last_modified == datetime.fromtimestamp(
        test_file.stat.return_value.st_mtime,
        tz=UTC,
    )


@pytest.mark.asyncio
async def test_create_or_update_file_stat_failure_fallback():
    """Stat failures should not crash file upsert and should fallback metadata."""
    session = AsyncMock()

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result_mock)
    session.flush = AsyncMock()

    test_file = MagicMock(spec=Path)
    test_file.read_text.return_value = "content"
    test_file.relative_to.return_value = Path("failed_stat.txt")
    test_file.stat.side_effect = OSError("stat failed")

    with patch("src.workers.file_worker.RepositoryManager") as mock_repo_manager:
        mock_manager = MagicMock()
        mock_manager.detect_language.return_value = LanguageEnum.PYTHON
        mock_repo_manager.return_value = mock_manager

        file_record = await _create_or_update_file(
            session,
            1,
            test_file,
            Path("."),
        )

    assert file_record.size_bytes == 0
    assert file_record.last_modified is None


@pytest.mark.asyncio
async def test_generate_repository_embeddings_no_chunks():
    """Test embedding generation with no chunks."""
    session = AsyncMock()
    
    # Mock no chunks found
    # Use MagicMock for Result object because scalar(), scalars(), all() are synchronous
    result_mock = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    result_mock.scalars.return_value = mock_scalars
    
    session.execute = AsyncMock(return_value=result_mock)
    
    count = await _generate_repository_embeddings(session, 1)
    
    assert count == 0


@pytest.mark.asyncio
async def test_generate_repository_embeddings_success(mock_chunk):
    """Test successful embedding generation."""
    session = AsyncMock()
    
    # Mock chunks found
    result_mock = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_chunk]
    result_mock.scalars.return_value = mock_scalars
    session.execute = AsyncMock(return_value=result_mock)
    
    with patch('src.workers.embedding_worker.EmbeddingGenerator') as mock_generator_class, \
         patch('src.workers.embedding_worker.PgVectorStore') as mock_store_class:
            
            # Mock embedding generator
            mock_generator = AsyncMock()
            mock_generator.generate_embeddings.return_value = [
                {'chunk_id': 1, 'vector': [0.1] * FIXED_EMBEDDING_DIMENSION}
            ]
            mock_generator_class.return_value = mock_generator
            
            # Mock vector store
            mock_store = AsyncMock()
            mock_store.store_embeddings.return_value = 1
            mock_store_class.return_value = mock_store
            
            count = await _generate_repository_embeddings(session, 1)
            
            assert count == 1
            assert mock_generator.generate_embeddings.called
            assert mock_store.store_embeddings.called


@pytest.mark.asyncio
async def test_job_monitor_retry_updates_celery_task_id():
    """
    Regression test for orphaned jobs on retry.
    Test that JobMonitor.retry_failed_job updates the celery_task_id to the NEW task ID.
    """
    session = AsyncMock()
    
    # Mock failed job
    mock_job = MagicMock(spec=Job)
    mock_job._sa_instance_state = MagicMock() 
    mock_job.id = 1
    mock_job.repository_id = 123
    mock_job.job_type = "sync_repository"
    mock_job.status = JobStatusEnum.FAILED
    mock_job.celery_task_id = "old-task-id-123"
    mock_job.retry_count = 0
    mock_job.max_retries = 3
    mock_job.job_metadata = {}
    
    # Mock repository
    mock_repo = MagicMock(spec=Repository)
    mock_repo._sa_instance_state = MagicMock()
    mock_repo.status = RepositoryStatusEnum.FAILED
    
    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = mock_job
    
    repo_result = MagicMock()
    repo_result.scalar_one_or_none.return_value = mock_repo
    
    session.execute = AsyncMock(side_effect=[job_result, repo_result])
    session.commit = AsyncMock()
    
    # Mock the Celery task (sync_repository) which is imported locally in job_monitor
    # We patch where it comes from (src.workers.tasks)
    with patch('src.workers.tasks.sync_repository') as mock_sync_task:
        
        # Mock apply_async to succeed
        mock_sync_task.apply_async.return_value = None
        
        monitor = JobMonitor(session)
        result = await monitor.retry_failed_job(mock_job.id)
        
        # Verify retry initiated
        assert result is True
        assert mock_sync_task.apply_async.called
        
        # Verify celery_task_id was updated
        assert mock_job.celery_task_id != "old-task-id-123"
        # Since we mock uuid inside the code (or rely on real uuid), it's a random UUID.
        assert len(mock_job.celery_task_id) == 36


@pytest.mark.asyncio
async def test_count_symbols_efficient():
    """
    Regression test for inefficient symbol counting.
    Test that _count_symbols uses SQL COUNT instead of loading all rows.
    """
    session = AsyncMock()
    
    # Mock result with count
    result_mock = MagicMock() # MUST be MagicMock for sync .scalar() call
    result_mock.scalar.return_value = 42
    session.execute = AsyncMock(return_value=result_mock)
    
    count = await _count_symbols(session, 1)
    
    # Verify it used COUNT query
    assert count == 42
    
    # Verify execute was called with a count query
    call_args = session.execute.call_args[0][0]
    query_str = str(call_args)
    assert "count" in query_str.lower()

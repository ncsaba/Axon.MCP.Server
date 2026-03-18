import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from src.utils.system_context_generator import SystemContextGenerator
from src.workers.system_context_worker import _generate_context_async
from src.mcp_server.tools.system_map import get_system_map
from src.config.enums import LanguageEnum, RepositoryStatusEnum, SymbolKindEnum
from src.database.models import Repository, FileInstance as File, Symbol

@pytest.mark.asyncio
@pytest.mark.integration
async def test_system_context_generator(async_session):
    """Generate system context from real persisted repository/file/symbol rows."""
    repo = Repository(
        gitlab_project_id=2001,
        name="TestRepo",
        path_with_namespace="test/repo",
        url="https://example.com/test/repo.git",
        clone_url="https://example.com/test/repo.git",
        default_branch="main",
        description="Test Desc",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    file = File(
        repository_id=repo.id,
        path="src/test.py",
        language=LanguageEnum.PYTHON,
        size_bytes=128,
    )
    async_session.add(file)
    await async_session.flush()

    symbol = Symbol(
        file_instance_id=file.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="TestClass",
        fully_qualified_name="test.TestClass",
        start_line=1,
        end_line=10,
        documentation="Docs",
        ai_enrichment={"functional_summary": "Test Summary"},
    )
    async_session.add(symbol)
    await async_session.flush()

    generator = SystemContextGenerator(async_session)
    context = await generator.generate_system_map(repository_id=repo.id)
    
    assert "generated_at" in context
    assert len(context["repositories"]) == 1
    assert context["repositories"][0]["name"] == "TestRepo"
    assert context["repositories"][0]["language"] == "PYTHON"
    assert len(context["key_modules"]) >= 1
    assert context["key_modules"][0]["name"] == "TestClass"
    assert context["key_modules"][0]["description"] == "Test Summary"
    # Ensure generated_at is an ISO format string (basic check)
    assert "T" in context["generated_at"]

@pytest.mark.asyncio
async def test_worker_caching():
    """Test that worker caches the result."""
    with patch("src.workers.system_context_worker.AsyncSessionLocal") as MockSession, \
         patch("src.workers.system_context_worker.get_cache") as mock_get_cache, \
         patch("src.workers.system_context_worker.get_distributed_lock") as mock_get_lock, \
         patch("src.workers.system_context_worker.SystemContextGenerator") as MockGen:
         
         # Mock Cache
         mock_cache_instance = AsyncMock()
         mock_get_cache.return_value = mock_cache_instance
         
         # Mock Lock
         mock_lock = MagicMock()
         mock_get_lock.return_value = mock_lock
         # Mock context manager for lock.acquire
         mock_lock_ctx = MagicMock()
         mock_lock_ctx.__enter__.return_value = True # acquired = True
         mock_lock.acquire.return_value = mock_lock_ctx

         # Mock Generator
         instance = MockGen.return_value
         instance.generate_system_map = AsyncMock(return_value={"test": "context", "generated_at": "now"})
         
         # Mock session
         mock_session = AsyncMock()
         mock_session.__aenter__.return_value = mock_session
         MockSession.return_value = mock_session

         await _generate_context_async(repository_id=None)
         
         mock_cache_instance.set.assert_called_once()
         call_args = mock_cache_instance.set.call_args
         assert call_args[0][0] == "system_context_map"  # Key
         assert call_args[0][1] == {"test": "context", "generated_at": "now"}   # Value

@pytest.mark.asyncio
async def test_mcp_tool_retrieval():
    """Test tool interaction with cache."""
    mock_ctx = MagicMock()
    
    # CASE 1: Cache Hit
    with patch("src.mcp_server.tools.system_map.get_cache") as mock_get_cache:
        mock_cache_instance = AsyncMock()
        mock_cache_instance.get.return_value = "{'cached': 'data'}"
        mock_get_cache.return_value = mock_cache_instance
        
        result = await get_system_map(mock_ctx)
        assert result == "{'cached': 'data'}"

    # CASE 2: Cache Miss (Trigger worker)
    with patch("src.mcp_server.tools.system_map.get_cache") as mock_get_cache, \
         patch("src.mcp_server.tools.system_map.generate_context") as mock_task:
        
        mock_cache_instance = AsyncMock()
        mock_cache_instance.get.return_value = None
        mock_get_cache.return_value = mock_cache_instance
        
        result = await get_system_map(mock_ctx)
        
        assert "System map is being generated" in result
        mock_task.delay.assert_called_once()

"""Unit tests for MCP server tools and response formatting."""

from typing import Any, List
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.enums import (
    AccessModifierEnum,
    LanguageEnum,
    RepositoryStatusEnum,
    SymbolKindEnum,
    SourceControlProviderEnum,
    RelationTypeEnum
)
from src.database.models import FileInstance as File, Repository, Symbol, Chunk, Relation
from src.mcp_server.server import AxonMCPServer
from src.mcp_server.tools.router import route_tool_call
from src.mcp_server.formatters.search import format_search_results
from src.mcp_server.formatters.repository import format_repository_list
from src.mcp_server.formatters.symbols import format_symbol_context


@pytest.fixture
def mock_session():
    """Create a mock async session."""
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock()
    # Support async context manager
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    return session


@pytest.mark.asyncio
class TestMCPServer:
    """Test MCP server functionality with mocks."""

    async def test_mcp_server_initialization(self):
        """Test MCP server initializes correctly."""
        server = AxonMCPServer()
        assert server is not None
        assert server.server is not None
        assert server.server.name == "axon-mcp-server"

    async def test_search_code_response_format(self, mock_session):
        """Test search_code returns proper MCP response format."""
        
        # Mock SearchResult object (mimic structure expected by formatters)
        mock_result = MagicMock()
        mock_result.symbol_id = 1
        mock_result.file_id = 2
        mock_result.repository_id = 3
        mock_result.name = "test_function"
        mock_result.kind = SymbolKindEnum.FUNCTION
        mock_result.signature = "def test_function():"
        mock_result.fully_qualified_name = "test.test_function"
        mock_result.file_path = "test.py"
        mock_result.repository_name = "test-repo"
        mock_result.start_line = 1
        mock_result.end_line = 5
        mock_result.code_snippet = "def test_function():\n    pass"
        mock_result.documentation = "A test function"
        mock_result.score = 0.95
        mock_result.match_type = "hybrid"
        mock_result.context_url = "http://test"

        # Patch SearchService
        with patch("src.mcp_server.tools.search.get_async_session", return_value=mock_session), \
             patch("src.mcp_server.tools.search.SearchService") as MockSearchService:
            
            # Configure service mock
            service_instance = MockSearchService.return_value
            service_instance.search = AsyncMock(return_value=[mock_result])

            result = await route_tool_call(
                "search_code", {"query": "test_function", "limit": 10}
            )

            # Verify MCP response format
            assert isinstance(result, list)
            assert len(result) > 0
            assert result[0].type == "text"
            assert "test_function" in result[0].text

    async def test_get_symbol_context_response_format(self, mock_session):
        """Test get_symbol_context returns proper MCP response format."""
        
        # Create mock data objects
        repo = MagicMock(spec=Repository)
        repo.id = 1
        repo.name = "test-repo"
        
        file = MagicMock(spec=File)
        file.id = 2
        file.path = "test.py"
        file.language = LanguageEnum.PYTHON
        
        symbol = MagicMock(spec=Symbol)
        symbol.id = 3
        symbol.name = "test_class"
        symbol.kind = SymbolKindEnum.CLASS
        symbol.fully_qualified_name = "test.TestClass"
        symbol.signature = "class TestClass:"
        symbol.documentation = "A test class"
        symbol.start_line = 1
        symbol.end_line = 10
        symbol.parameters = []
        symbol.return_type = "None"
        symbol.complexity = 1
        symbol.access_modifier = AccessModifierEnum.PUBLIC
        symbol.parent_symbol_id = None
        
        # Setup session.execute side effects for sequence of queries
        # 1. Symbol lookup
        mock_row_1 = MagicMock()
        mock_row_1.__iter__.return_value = [symbol, file, repo]
        mock_result_1 = MagicMock()
        mock_result_1.first.return_value = [symbol, file, repo]
        
        # 2. Chunk lookup
        mock_result_2 = MagicMock()
        mock_result_2.first.return_value = ["class TestClass:\n    pass"]
        
        # 3. Outgoing relations
        mock_result_3 = MagicMock()
        mock_result_3.all.return_value = []
        
        # 4. Incoming relations
        mock_result_4 = MagicMock()
        mock_result_4.all.return_value = []
        
        mock_session.execute.side_effect = [
             mock_result_1, 
             mock_result_2, 
             mock_result_3, 
             mock_result_4
        ]

        with patch("src.mcp_server.tools.symbols.get_async_session", return_value=mock_session), \
             patch("src.mcp_server.tools.symbols.get_connected_endpoints_for_symbol", new_callable=AsyncMock) as mock_endpoints:
            
            mock_endpoints.return_value = {}

            result = await route_tool_call(
                "get_symbol_context",
                {"symbol_id": 3, "include_relationships": True}
            )

            # Verify MCP response format
            assert isinstance(result, list)
            assert len(result) > 0
            assert result[0].type == "text"
            assert "test_class" in result[0].text.lower()

    async def test_get_symbol_context_not_found(self, mock_session):
        """Test get_symbol_context with non-existent symbol ID."""
        
        # Mock empty result
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.execute.return_value = mock_result

        with patch("src.mcp_server.tools.symbols.get_async_session", return_value=mock_session):

            result = await route_tool_call(
                "get_symbol_context",
                {"symbol_id": 99999, "include_relationships": False}
            )

            assert isinstance(result, list)
            assert "Symbol not found" in result[0].text

    async def test_list_repositories_response_format(self, mock_session):
        """Test list_repositories returns proper MCP response format."""
        
        # Mock repos
        repo = MagicMock(spec=Repository)
        repo.id = 1
        repo.name = "repo-1"
        repo.provider = SourceControlProviderEnum.GITLAB
        repo.path_with_namespace = "test/repo-1"
        repo.url = "http://test"
        repo.clone_url = "http://test.git"
        repo.default_branch = "main"
        repo.status = RepositoryStatusEnum.COMPLETED
        repo.total_files = 10
        repo.total_symbols = 100
        repo.size_bytes = 1000
        repo.last_synced_at = datetime.now(timezone.utc)
        repo.created_at = datetime.now(timezone.utc)
        repo.last_commit_sha = "abc"
        repo.gitlab_project_id = 123
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [repo]
        mock_session.execute.return_value = mock_result

        with patch("src.mcp_server.tools.repository.get_async_session", return_value=mock_session):

            result = await route_tool_call("list_repositories", {"limit": 20})

            # Verify MCP response format
            assert isinstance(result, list)
            assert len(result) > 0
            assert result[0].type == "text"
            assert "repo-1" in result[0].text

    async def test_list_repositories_empty(self, mock_session):
        """Test list_repositories when no repositories exist."""
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("src.mcp_server.tools.repository.get_async_session", return_value=mock_session):

            result = await route_tool_call("list_repositories", {"limit": 20})

            assert isinstance(result, list)
            assert "No repositories available" in result[0].text

    async def test_search_code_error_handling(self, mock_session):
        """Test search_code handles errors gracefully."""

        with patch("src.mcp_server.tools.search.get_async_session", return_value=mock_session), \
             patch("src.mcp_server.tools.search.SearchService") as MockSearchService:
            
            MockSearchService.return_value.search.side_effect = Exception("Database error")

            # We expect route_tool_call to catch exception and return error message
            result = await route_tool_call("search_code", {"query": "test", "limit": 10})

            assert isinstance(result, list)
            assert "Search failed" in result[0].text


@pytest.mark.asyncio
class TestMCPToolResponseFormatting:
    """Test MCP tool response formatting."""

    async def test_formatting_methods_exist(self):
        """Test that formatting methods exist and work."""
        # Test format_search_results
        results = [
            {
                "name": "test_func",
                "kind": "function",
                "signature": "def test_func():",
                "file": "test.py",
                "repository": "test-repo",
                "lines": "1-5",
                "documentation": "Test function",
                "relevance_score": 0.95,
                "symbol_id": 1,
            }
        ]
        formatted = format_search_results(results, "test")
        assert "test" in formatted
        assert "_func" in formatted
        assert "test-repo" in formatted

        # Test format_repository_list
        repos = [
            {
                "id": 1,
                "name": "test-repo",
                "provider": "gitlab",
                "path_with_namespace": "test/repo",
                "default_branch": "main",
                "status": "completed",
                "total_files": 10,
                "total_symbols": 100,
                "size_bytes": 1024,
                "last_synced": "2024-01-01T00:00:00",
                "url": "https://gitlab.com/test/repo",
            }
        ]
        formatted = format_repository_list(repos)
        assert "test-repo" in formatted
        assert "completed" in formatted

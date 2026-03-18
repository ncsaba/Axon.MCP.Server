from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config.enums import LanguageEnum, SymbolKindEnum
from src.embeddings.chunk_context import ChunkContext
from src.extractors.knowledge_extractor import KnowledgeExtractor
from src.parsers.base_parser import ParsedSymbol


@pytest.mark.asyncio
async def test_load_source_file_content_returns_none_for_missing_path():
    """Missing source files should not break chunk generation."""
    extractor = KnowledgeExtractor(AsyncMock())

    assert await extractor._load_source_file_content("/tmp/axon-missing-source-file.py") is None


@pytest.mark.asyncio
async def test_create_chunks_for_symbol_passes_loaded_file_content_to_chunker():
    """Chunk creation should forward source text so implementation bodies can be embedded."""
    session = AsyncMock()
    file_row = SimpleNamespace(id=1, path="src/app/main.py", current_content_id=99)
    session.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: file_row)

    extractor = KnowledgeExtractor(session)
    symbol = SimpleNamespace(id=7, start_line=2, end_line=4)
    parsed_symbol = ParsedSymbol(
        kind=SymbolKindEnum.FUNCTION,
        name="run",
        fully_qualified_name="app.run",
        start_line=2,
        end_line=4,
        start_column=0,
        end_column=0,
        signature="def run() -> str:",
    )
    context = ChunkContext(file_path=file_row.path)

    extractor.context_builder.build_context = AsyncMock(return_value=context)
    extractor.chunker.create_chunks_for_symbol = AsyncMock(
        return_value=[
            {
                "content": "def run() -> str:\n    return 'ok'",
                "content_type": "code",
                "chunk_subtype": "implementation",
                "context_metadata": {"imports": []},
                "start_line": 2,
                "end_line": 4,
            }
        ]
    )

    chunks = await extractor._create_chunks_for_symbol(
        symbol,
        parsed_symbol,
        file_id=1,
        file_content="def run() -> str:\n    return 'ok'\n",
    )

    extractor.context_builder.build_context.assert_awaited_once_with(
        symbol,
        file_row,
        file_content="def run() -> str:\n    return 'ok'\n",
    )
    extractor.chunker.create_chunks_for_symbol.assert_awaited_once_with(
        symbol,
        file_row,
        context,
        file_content="def run() -> str:\n    return 'ok'\n",
    )
    assert len(chunks) == 1
    assert chunks[0].file_content_id == 99


@pytest.mark.asyncio
async def test_create_fallback_chunks_for_file_uses_policy_for_parser_empty_file():
    """Parser-empty config files should still produce bounded semantic chunks."""
    extractor = KnowledgeExtractor(AsyncMock())
    file_row = SimpleNamespace(
        id=1,
        path="src/main/resources/application.yml",
        current_content_id=77,
        language=LanguageEnum.UNKNOWN,
    )

    chunks = extractor._create_fallback_chunks_for_file(
        file_obj=file_row,
        file_content="spring:\n  datasource:\n    url: jdbc:h2:mem:test\n",
    )

    assert len(chunks) == 1
    assert chunks[0].file_content_id == 77
    assert chunks[0].chunk_subtype == "config"
    assert "jdbc:h2:mem:test" in chunks[0].content


@pytest.mark.asyncio
async def test_create_fallback_file_symbol_marks_file_backed_semantic_symbol():
    """Fallback file symbols should be explicit and stable."""
    extractor = KnowledgeExtractor(AsyncMock())
    file_row = SimpleNamespace(
        id=5,
        commit_id=None,
        path="build/build.xml",
        language=LanguageEnum.UNKNOWN,
    )
    symbol = await extractor._create_fallback_file_symbol(
        file_obj=file_row,
        file_content="<project></project>\n",
    )

    assert symbol is not None
    assert symbol.kind == SymbolKindEnum.MODULE
    assert symbol.fully_qualified_name == "file::build/build.xml"
    assert symbol.structured_docs["fallback_file_symbol"] is True

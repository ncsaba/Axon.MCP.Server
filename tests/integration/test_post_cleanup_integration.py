from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from src.config.enums import LanguageEnum, RepositoryStatusEnum
from src.database.models import Chunk, ChunkSymbolLink, FileInstance as File, FileContent, Repository, Symbol
from src.extractors.knowledge_extractor import KnowledgeExtractor
from src.parsers import ParserFactory
from src.workers.file_worker import DEFAULT_PARSER_FINGERPRINT


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_pgvector_extension_available(async_session):
    """Verify the real PostgreSQL test instance has pgvector enabled."""
    result = await async_session.execute(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    )
    assert result.scalar_one_or_none() == "vector"


@pytest.mark.asyncio
async def test_parser_factory_routes_python_and_java():
    """Verify parser routing uses the current language surface."""
    python_parser = ParserFactory.get_parser_for_file(Path("service.py"))
    java_parser = ParserFactory.get_parser_for_file(Path("App.java"))
    appsettings_parser = ParserFactory.get_parser_for_file(Path("appsettings.Development.json"))

    assert python_parser.get_language() == LanguageEnum.PYTHON
    assert java_parser.get_language() == LanguageEnum.JAVA
    # appsettings parser is JSON-based and mapped as JavaScript in current model.
    assert appsettings_parser.get_language() == LanguageEnum.JAVASCRIPT


@pytest.mark.asyncio
async def test_python_parse_and_extract_roundtrip(async_session):
    """Run parser + extraction + persistence against the real database."""
    repo = Repository(
        gitlab_project_id=999001,
        name="integration-repo",
        path_with_namespace="integration/repo",
        url="https://example.com/integration/repo.git",
        clone_url="https://example.com/integration/repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    content = """
class Greeter:
    def greet(self, name: str) -> str:
        return f"Hello {name}"


def run() -> str:
    greeter = Greeter()
    return greeter.greet("World")
"""
    file_content = FileContent(
        content_hash="integration-roundtrip-content",
        language=LanguageEnum.PYTHON,
        parser_fingerprint=DEFAULT_PARSER_FINGERPRINT,
        size_bytes=len(content.encode("utf-8")),
        line_count=len(content.splitlines()),
    )
    async_session.add(file_content)
    await async_session.flush()

    file = File(
        repository_id=repo.id,
        path="src/app/main.py",
        language=LanguageEnum.PYTHON,
        size_bytes=256,
        current_content_id=file_content.id,
        content_hash=file_content.content_hash,
        line_count=file_content.line_count,
    )
    async_session.add(file)
    await async_session.flush()

    parser = ParserFactory.get_parser_for_file(Path(file.path))
    parse_result = parser.parse(content, file.path)
    assert not parse_result.parse_errors
    assert len(parse_result.symbols) > 0

    extractor = KnowledgeExtractor(async_session)
    extraction = await extractor.extract_and_persist(parse_result, file.id)
    await async_session.flush()

    assert extraction.symbols_created > 0
    assert extraction.chunks_created > 0
    assert not extraction.errors

    symbol_count = await async_session.scalar(
        select(func.count()).select_from(Symbol).where(Symbol.file_instance_id == file.id)
    )
    chunk_count = await async_session.scalar(
        select(func.count(func.distinct(Chunk.id)))
        .select_from(Chunk)
        .join(ChunkSymbolLink, ChunkSymbolLink.chunk_id == Chunk.id)
        .where(ChunkSymbolLink.file_instance_id == file.id)
    )

    assert symbol_count and symbol_count > 0
    assert chunk_count and chunk_count > 0


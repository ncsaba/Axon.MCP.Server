from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import LanguageEnum, RelationTypeEnum, RepositoryStatusEnum, SourceControlProviderEnum, SymbolKindEnum
from src.config.settings import get_settings
from src.database.models import File, Relation, Repository, Symbol
from src.extractors.call_graph_builder import CallGraphBuilder


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_java_call_relationships_are_created(async_session):
    """Build Java CALLS relations for a minimal same-class invocation fixture.

    Temporary validation test for the Java call vertical slice.
    Remove after the Java semantic extractor set is considered stable.
    """
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999003,
        name="java-call-repo",
        path_with_namespace="integration/java-call-repo",
        url="https://example.com/integration/java-call-repo.git",
        clone_url="https://example.com/integration/java-call-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    repo_cache_root = Path(get_settings().repo_cache_dir).resolve()
    repo_disk_path = repo_cache_root / repo.path_with_namespace.replace("/", "_")
    file_rel_path = Path("src/main/java/com/example/app/App.java")
    file_abs_path = repo_disk_path / file_rel_path
    file_abs_path.parent.mkdir(parents=True, exist_ok=True)
    file_abs_path.write_text(
        "\n".join(
            [
                "package com.example.app;",
                "public class App {",
                "  public void helper() {}",
                "  public void run() { helper(); }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    file = File(
        repository_id=repo.id,
        path=str(file_rel_path),
        language=LanguageEnum.JAVA,
        size_bytes=file_abs_path.stat().st_size,
    )
    async_session.add(file)
    await async_session.flush()

    helper_symbol = Symbol(
        file_id=file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="helper",
        fully_qualified_name="App.helper",
        parent_name="App",
        start_line=3,
        end_line=3,
    )
    run_symbol = Symbol(
        file_id=file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="run",
        fully_qualified_name="App.run",
        parent_name="App",
        start_line=4,
        end_line=4,
    )
    async_session.add_all([helper_symbol, run_symbol])
    await async_session.flush()
    await async_session.commit()

    builder = CallGraphBuilder(async_session)
    created = await builder.build_call_relationships(repo.id)
    assert created >= 1

    relation_result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == run_symbol.id,
            Relation.to_symbol_id == helper_symbol.id,
            Relation.relation_type == RelationTypeEnum.CALLS,
        )
    )
    assert relation_result.scalar_one_or_none() is not None

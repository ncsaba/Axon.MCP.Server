from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import LanguageEnum, RelationTypeEnum, RepositoryStatusEnum, SourceControlProviderEnum, SymbolKindEnum
from src.config.settings import get_settings
from src.database.models import FileInstance as File, Relation, Repository, Symbol
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
        file_instance_id=file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="helper",
        fully_qualified_name="App.helper",
        parent_name="App",
        start_line=3,
        end_line=3,
    )
    run_symbol = Symbol(
        file_instance_id=file.id,
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


@pytest.mark.asyncio
async def test_java_static_call_relationships_are_created(async_session):
    """Qualified static calls should resolve to methods on imported classes."""
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999104,
        name="java-static-call-repo",
        path_with_namespace="integration/java-static-call-repo",
        url="https://example.com/integration/java-static-call-repo.git",
        clone_url="https://example.com/integration/java-static-call-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    repo_cache_root = Path(get_settings().repo_cache_dir).resolve()
    repo_disk_path = repo_cache_root / repo.path_with_namespace.replace("/", "_")

    app_rel = Path("src/main/java/com/example/app/App.java")
    constants_rel = Path("src/main/java/com/example/util/Constants.java")
    app_abs = repo_disk_path / app_rel
    constants_abs = repo_disk_path / constants_rel
    app_abs.parent.mkdir(parents=True, exist_ok=True)
    constants_abs.parent.mkdir(parents=True, exist_ok=True)

    app_abs.write_text(
        "\n".join(
            [
                "package com.example.app;",
                "import com.example.util.Constants;",
                "public class App {",
                "  public void run() { Constants.defaultLimit(); }",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    constants_abs.write_text(
        "\n".join(
            [
                "package com.example.util;",
                "public class Constants {",
                "  public static int defaultLimit() { return 42; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    app_file = File(repository_id=repo.id, path=str(app_rel), language=LanguageEnum.JAVA, size_bytes=app_abs.stat().st_size)
    constants_file = File(repository_id=repo.id, path=str(constants_rel), language=LanguageEnum.JAVA, size_bytes=constants_abs.stat().st_size)
    async_session.add_all([app_file, constants_file])
    await async_session.flush()

    run_symbol = Symbol(
        file_instance_id=app_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="run",
        fully_qualified_name="com.example.app.App.run",
        parent_name="com.example.app.App",
        start_line=4,
        end_line=4,
    )
    constants_class_symbol = Symbol(
        file_instance_id=constants_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="Constants",
        fully_qualified_name="com.example.util.Constants",
        start_line=2,
        end_line=4,
    )
    default_limit_symbol = Symbol(
        file_instance_id=constants_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="defaultLimit",
        fully_qualified_name="com.example.util.Constants.defaultLimit",
        parent_name="com.example.util.Constants",
        start_line=3,
        end_line=3,
    )
    async_session.add_all([run_symbol, constants_class_symbol, default_limit_symbol])
    await async_session.flush()
    await async_session.commit()

    builder = CallGraphBuilder(async_session)
    created = await builder.build_call_relationships(repo.id)
    assert created >= 1

    relation_result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == run_symbol.id,
            Relation.to_symbol_id == default_limit_symbol.id,
            Relation.relation_type == RelationTypeEnum.CALLS,
        )
    )
    assert relation_result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_java_overloaded_call_prefers_matching_arity(async_session):
    """Overloaded method resolution should prefer candidates with matching argument count."""
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999105,
        name="java-overload-call-repo",
        path_with_namespace="integration/java-overload-call-repo",
        url="https://example.com/integration/java-overload-call-repo.git",
        clone_url="https://example.com/integration/java-overload-call-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    repo_cache_root = Path(get_settings().repo_cache_dir).resolve()
    repo_disk_path = repo_cache_root / repo.path_with_namespace.replace("/", "_")
    file_rel = Path("src/main/java/com/example/app/App.java")
    file_abs = repo_disk_path / file_rel
    file_abs.parent.mkdir(parents=True, exist_ok=True)
    file_abs.write_text(
        "\n".join(
            [
                "package com.example.app;",
                "public class App {",
                "  public void helper() {}",
                "  public void helper(String name) {}",
                "  public void run() { helper(\"x\"); }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    file = File(repository_id=repo.id, path=str(file_rel), language=LanguageEnum.JAVA, size_bytes=file_abs.stat().st_size)
    async_session.add(file)
    await async_session.flush()

    helper_no_arg_symbol = Symbol(
        file_instance_id=file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="helper",
        fully_qualified_name="com.example.app.App.helper0",
        parent_name="com.example.app.App",
        parameters=[],
        start_line=3,
        end_line=3,
    )
    helper_one_arg_symbol = Symbol(
        file_instance_id=file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="helper",
        fully_qualified_name="com.example.app.App.helper1",
        parent_name="com.example.app.App",
        parameters=[{"name": "name", "type": "String"}],
        start_line=4,
        end_line=4,
    )
    run_symbol = Symbol(
        file_instance_id=file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="run",
        fully_qualified_name="com.example.app.App.run",
        parent_name="com.example.app.App",
        start_line=5,
        end_line=5,
    )
    async_session.add_all([helper_no_arg_symbol, helper_one_arg_symbol, run_symbol])
    await async_session.flush()
    await async_session.commit()

    builder = CallGraphBuilder(async_session)
    created = await builder.build_call_relationships(repo.id)
    assert created >= 1

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == run_symbol.id,
            Relation.relation_type == RelationTypeEnum.CALLS,
        )
    )
    target_ids = {rel.to_symbol_id for rel in result.scalars().all()}
    assert helper_one_arg_symbol.id in target_ids
    assert helper_no_arg_symbol.id not in target_ids

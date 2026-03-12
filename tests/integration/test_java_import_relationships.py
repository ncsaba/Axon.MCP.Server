from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import LanguageEnum, RelationTypeEnum, RepositoryStatusEnum, SourceControlProviderEnum, SymbolKindEnum
from src.database.models import File, Relation, Repository, Symbol
from src.extractors.import_resolver import ImportRelationshipBuilder


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_java_import_relationships_are_created(async_session, tmp_path: Path):
    """Build Java import relations for a minimal multi-file fixture.

    Temporary validation test for the Java import vertical slice.
    Remove after the Java semantic extractor set is considered stable.
    """
    app_file_rel = Path("src/main/java/com/example/app/App.java")
    service_file_rel = Path("src/main/java/com/example/services/UserService.java")

    app_file_abs = tmp_path / app_file_rel
    service_file_abs = tmp_path / service_file_rel
    app_file_abs.parent.mkdir(parents=True, exist_ok=True)
    service_file_abs.parent.mkdir(parents=True, exist_ok=True)

    app_file_abs.write_text(
        "\n".join(
            [
                "package com.example.app;",
                "import com.example.services.UserService;",
                "public class App {",
                "  private UserService service;",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    service_file_abs.write_text(
        "\n".join(
            [
                "package com.example.services;",
                "public class UserService {",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999002,
        name="java-import-repo",
        path_with_namespace="integration/java-import-repo",
        url="https://example.com/integration/java-import-repo.git",
        clone_url="https://example.com/integration/java-import-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    app_file = File(
        repository_id=repo.id,
        path=str(app_file_rel),
        language=LanguageEnum.JAVA,
        size_bytes=app_file_abs.stat().st_size,
    )
    service_file = File(
        repository_id=repo.id,
        path=str(service_file_rel),
        language=LanguageEnum.JAVA,
        size_bytes=service_file_abs.stat().st_size,
    )
    async_session.add_all([app_file, service_file])
    await async_session.flush()

    app_symbol = Symbol(
        file_id=app_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="App",
        fully_qualified_name="com.example.app.App",
        start_line=3,
        end_line=5,
    )
    service_symbol = Symbol(
        file_id=service_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="UserService",
        fully_qualified_name="com.example.services.UserService",
        start_line=2,
        end_line=3,
    )
    async_session.add_all([app_symbol, service_symbol])
    await async_session.flush()

    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)

    assert created >= 1

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == app_symbol.id,
            Relation.to_symbol_id == service_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    relation = result.scalar_one_or_none()
    assert relation is not None

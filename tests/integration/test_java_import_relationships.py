from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import LanguageEnum, RelationTypeEnum, RepositoryStatusEnum, SourceControlProviderEnum, SymbolKindEnum
from src.database.models import FileInstance as File, Relation, Repository, Symbol
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
        file_instance_id=app_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="App",
        fully_qualified_name="com.example.app.App",
        start_line=3,
        end_line=5,
    )
    service_symbol = Symbol(
        file_instance_id=service_file.id,
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


@pytest.mark.asyncio
async def test_java_package_wildcard_import_relationships_are_created(async_session, tmp_path: Path):
    """Java package wildcard imports should create IMPORTS edges to package type symbols."""
    app_file_rel = Path("src/main/java/com/example/app/App.java")
    service_file_rel = Path("src/main/java/com/example/services/UserService.java")
    billing_file_rel = Path("src/main/java/com/example/services/BillingService.java")

    app_file_abs = tmp_path / app_file_rel
    service_file_abs = tmp_path / service_file_rel
    billing_file_abs = tmp_path / billing_file_rel
    app_file_abs.parent.mkdir(parents=True, exist_ok=True)
    service_file_abs.parent.mkdir(parents=True, exist_ok=True)
    billing_file_abs.parent.mkdir(parents=True, exist_ok=True)

    app_file_abs.write_text(
        "\n".join(
            [
                "package com.example.app;",
                "import com.example.services.*;",
                "public class App {",
                "  private UserService userService;",
                "  private BillingService billingService;",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    service_file_abs.write_text(
        "\n".join(["package com.example.services;", "public class UserService {}"]),
        encoding="utf-8",
    )
    billing_file_abs.write_text(
        "\n".join(["package com.example.services;", "public class BillingService {}"]),
        encoding="utf-8",
    )

    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999102,
        name="java-wildcard-import-repo",
        path_with_namespace="integration/java-wildcard-import-repo",
        url="https://example.com/integration/java-wildcard-import-repo.git",
        clone_url="https://example.com/integration/java-wildcard-import-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    app_file = File(repository_id=repo.id, path=str(app_file_rel), language=LanguageEnum.JAVA, size_bytes=app_file_abs.stat().st_size)
    service_file = File(repository_id=repo.id, path=str(service_file_rel), language=LanguageEnum.JAVA, size_bytes=service_file_abs.stat().st_size)
    billing_file = File(repository_id=repo.id, path=str(billing_file_rel), language=LanguageEnum.JAVA, size_bytes=billing_file_abs.stat().st_size)
    async_session.add_all([app_file, service_file, billing_file])
    await async_session.flush()

    app_symbol = Symbol(
        file_instance_id=app_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="App",
        fully_qualified_name="com.example.app.App",
        start_line=3,
        end_line=6,
    )
    user_service_symbol = Symbol(
        file_instance_id=service_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="UserService",
        fully_qualified_name="com.example.services.UserService",
        start_line=2,
        end_line=2,
    )
    billing_service_symbol = Symbol(
        file_instance_id=billing_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="BillingService",
        fully_qualified_name="com.example.services.BillingService",
        start_line=2,
        end_line=2,
    )
    async_session.add_all([app_symbol, user_service_symbol, billing_service_symbol])
    await async_session.flush()

    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)
    assert created >= 2

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == app_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    target_ids = {rel.to_symbol_id for rel in result.scalars().all()}
    assert user_service_symbol.id in target_ids
    assert billing_service_symbol.id in target_ids


@pytest.mark.asyncio
async def test_java_static_wildcard_import_relationships_are_created(async_session, tmp_path: Path):
    """Static wildcard imports should create IMPORTS edges to imported members."""
    app_file_rel = Path("src/main/java/com/example/app/App.java")
    constants_file_rel = Path("src/main/java/com/example/util/Constants.java")

    app_file_abs = tmp_path / app_file_rel
    constants_file_abs = tmp_path / constants_file_rel
    app_file_abs.parent.mkdir(parents=True, exist_ok=True)
    constants_file_abs.parent.mkdir(parents=True, exist_ok=True)

    app_file_abs.write_text(
        "\n".join(
            [
                "package com.example.app;",
                "import static com.example.util.Constants.*;",
                "public class App {",
                "  int local = MAX_USERS;",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    constants_file_abs.write_text(
        "\n".join(
            [
                "package com.example.util;",
                "public class Constants {",
                "  public static final int MAX_USERS = 100;",
                "  public static int defaultLimit() { return MAX_USERS; }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999103,
        name="java-static-wildcard-import-repo",
        path_with_namespace="integration/java-static-wildcard-import-repo",
        url="https://example.com/integration/java-static-wildcard-import-repo.git",
        clone_url="https://example.com/integration/java-static-wildcard-import-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    app_file = File(repository_id=repo.id, path=str(app_file_rel), language=LanguageEnum.JAVA, size_bytes=app_file_abs.stat().st_size)
    constants_file = File(repository_id=repo.id, path=str(constants_file_rel), language=LanguageEnum.JAVA, size_bytes=constants_file_abs.stat().st_size)
    async_session.add_all([app_file, constants_file])
    await async_session.flush()

    app_symbol = Symbol(
        file_instance_id=app_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="App",
        fully_qualified_name="com.example.app.App",
        start_line=3,
        end_line=5,
    )
    constants_class_symbol = Symbol(
        file_instance_id=constants_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.CLASS,
        name="Constants",
        fully_qualified_name="com.example.util.Constants",
        start_line=2,
        end_line=5,
    )
    max_users_symbol = Symbol(
        file_instance_id=constants_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.VARIABLE,
        name="MAX_USERS",
        parent_name="com.example.util.Constants",
        fully_qualified_name="com.example.util.Constants.MAX_USERS",
        start_line=3,
        end_line=3,
    )
    default_limit_symbol = Symbol(
        file_instance_id=constants_file.id,
        language=LanguageEnum.JAVA,
        kind=SymbolKindEnum.METHOD,
        name="defaultLimit",
        parent_name="com.example.util.Constants",
        fully_qualified_name="com.example.util.Constants.defaultLimit",
        start_line=4,
        end_line=4,
    )
    async_session.add_all([app_symbol, constants_class_symbol, max_users_symbol, default_limit_symbol])
    await async_session.flush()

    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)
    assert created >= 2

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == app_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    target_ids = {rel.to_symbol_id for rel in result.scalars().all()}
    assert max_users_symbol.id in target_ids
    assert default_limit_symbol.id in target_ids

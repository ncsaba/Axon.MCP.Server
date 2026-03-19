from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import (
    LanguageEnum,
    RelationTypeEnum,
    RepositoryStatusEnum,
    SourceControlProviderEnum,
    SymbolKindEnum,
)
from src.database.models import FileInstance as File, Relation, Repository, Symbol
from src.extractors.import_resolver import ImportRelationshipBuilder


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_python_absolute_from_import_relationships_are_created(async_session, tmp_path: Path):
    service_file_rel = Path("src/pkg/service.py")
    models_file_rel = Path("src/pkg/models.py")

    service_file_abs = tmp_path / service_file_rel
    models_file_abs = tmp_path / models_file_rel
    service_file_abs.parent.mkdir(parents=True, exist_ok=True)
    models_file_abs.parent.mkdir(parents=True, exist_ok=True)

    service_file_abs.write_text(
        "\n".join(
            [
                "from pkg.models import User",
                "",
                "class Service:",
                "    def build(self) -> User:",
                "        return User()",
            ]
        ),
        encoding="utf-8",
    )
    models_file_abs.write_text(
        "\n".join(
            [
                "class User:",
                "    pass",
            ]
        ),
        encoding="utf-8",
    )

    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999202,
        name="python-import-repo",
        path_with_namespace="integration/python-import-repo",
        url="https://example.com/integration/python-import-repo.git",
        clone_url="https://example.com/integration/python-import-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    service_file = File(
        repository_id=repo.id,
        path=str(service_file_rel),
        language=LanguageEnum.PYTHON,
        size_bytes=service_file_abs.stat().st_size,
    )
    models_file = File(
        repository_id=repo.id,
        path=str(models_file_rel),
        language=LanguageEnum.PYTHON,
        size_bytes=models_file_abs.stat().st_size,
    )
    async_session.add_all([service_file, models_file])
    await async_session.flush()

    service_symbol = Symbol(
        file_instance_id=service_file.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="Service",
        fully_qualified_name="Service",
        start_line=3,
        end_line=5,
    )
    user_symbol = Symbol(
        file_instance_id=models_file.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="User",
        fully_qualified_name="User",
        start_line=1,
        end_line=2,
    )
    async_session.add_all([service_symbol, user_symbol])
    await async_session.flush()

    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)

    assert created >= 1

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == service_symbol.id,
            Relation.to_symbol_id == user_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_python_relative_module_import_relationships_are_created(async_session, tmp_path: Path):
    api_file_rel = Path("src/pkg/api.py")
    helpers_file_rel = Path("src/pkg/helpers.py")

    api_file_abs = tmp_path / api_file_rel
    helpers_file_abs = tmp_path / helpers_file_rel
    api_file_abs.parent.mkdir(parents=True, exist_ok=True)
    helpers_file_abs.parent.mkdir(parents=True, exist_ok=True)

    api_file_abs.write_text(
        "\n".join(
            [
                "from . import helpers",
                "",
                "class Api:",
                "    def build(self):",
                "        return helpers.transform()",
            ]
        ),
        encoding="utf-8",
    )
    helpers_file_abs.write_text(
        "\n".join(
            [
                "def transform():",
                "    return 'ok'",
            ]
        ),
        encoding="utf-8",
    )

    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999203,
        name="python-relative-import-repo",
        path_with_namespace="integration/python-relative-import-repo",
        url="https://example.com/integration/python-relative-import-repo.git",
        clone_url="https://example.com/integration/python-relative-import-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    api_file = File(
        repository_id=repo.id,
        path=str(api_file_rel),
        language=LanguageEnum.PYTHON,
        size_bytes=api_file_abs.stat().st_size,
    )
    helpers_file = File(
        repository_id=repo.id,
        path=str(helpers_file_rel),
        language=LanguageEnum.PYTHON,
        size_bytes=helpers_file_abs.stat().st_size,
    )
    async_session.add_all([api_file, helpers_file])
    await async_session.flush()

    api_symbol = Symbol(
        file_instance_id=api_file.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="Api",
        fully_qualified_name="Api",
        start_line=3,
        end_line=5,
    )
    transform_symbol = Symbol(
        file_instance_id=helpers_file.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.FUNCTION,
        name="transform",
        fully_qualified_name="transform",
        start_line=1,
        end_line=2,
    )
    async_session.add_all([api_symbol, transform_symbol])
    await async_session.flush()

    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)

    assert created >= 1

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == api_symbol.id,
            Relation.to_symbol_id == transform_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    assert result.scalar_one_or_none() is not None

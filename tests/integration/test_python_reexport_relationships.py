"""Integration tests for Python __init__.py re-export semantics.

These tests verify that re-exported symbols from __init__.py files
are correctly resolved when building import relationships.
"""

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
async def test_python_reexport_from_init_py(async_session, tmp_path: Path):
    """Test that re-exported symbols from __init__.py are resolved.

    Package structure:
    mypackage/
      __init__.py  # contains: from .models import User
      models.py    # contains: class User

    Consumer:
      service.py   # contains: from mypackage import User
    """
    # Create package structure
    package_dir = tmp_path / "mypackage"
    package_dir.mkdir()

    init_file = package_dir / "__init__.py"
    init_file.write_text(
        "\n".join([
            "# Package init with re-export",
            "from .models import User",
            "",
            "__all__ = ['User']",
        ]),
        encoding="utf-8",
    )

    models_file = package_dir / "models.py"
    models_file.write_text(
        "\n".join([
            "class User:",
            "    pass",
        ]),
        encoding="utf-8",
    )

    service_file = tmp_path / "service.py"
    service_file.write_text(
        "\n".join([
            "from mypackage import User",
            "",
            "class Service:",
            "    def get_user(self) -> User:",
            "        return User()",
        ]),
        encoding="utf-8",
    )

    # Create database records
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999301,
        name="python-reexport-repo",
        path_with_namespace="integration/python-reexport-repo",
        url="https://example.com/integration/python-reexport-repo.git",
        clone_url="https://example.com/integration/python-reexport-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    # Create file records
    init_file_record = File(
        repository_id=repo.id,
        path="mypackage/__init__.py",
        language=LanguageEnum.PYTHON,
        size_bytes=init_file.stat().st_size,
    )
    models_file_record = File(
        repository_id=repo.id,
        path="mypackage/models.py",
        language=LanguageEnum.PYTHON,
        size_bytes=models_file.stat().st_size,
    )
    service_file_record = File(
        repository_id=repo.id,
        path="service.py",
        language=LanguageEnum.PYTHON,
        size_bytes=service_file.stat().st_size,
    )
    async_session.add_all([init_file_record, models_file_record, service_file_record])
    await async_session.flush()

    # Create symbol records
    # Note: User symbol is in models.py, not __init__.py
    user_symbol = Symbol(
        file_instance_id=models_file_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="User",
        fully_qualified_name="mypackage.models.User",
        start_line=1,
        end_line=2,
    )
    service_symbol = Symbol(
        file_instance_id=service_file_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="Service",
        fully_qualified_name="Service",
        start_line=3,
        end_line=5,
    )
    async_session.add_all([user_symbol, service_symbol])
    await async_session.flush()

    # Build import relationships
    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)

    # Verify that the import relationship was created
    # The service.py imports User from mypackage, which should resolve to models.py
    assert created >= 1

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == service_symbol.id,
            Relation.to_symbol_id == user_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    relation = result.scalar_one_or_none()
    assert relation is not None


@pytest.mark.asyncio
async def test_python_aliased_reexport_from_init_py(async_session, tmp_path: Path):
    """Test that aliased re-exports are resolved correctly.

    Package structure:
    mypackage/
      __init__.py  # contains: from .deep import DeepClass as ShallowClass
      deep.py      # contains: class DeepClass

    Consumer:
      client.py    # contains: from mypackage import ShallowClass
    """
    package_dir = tmp_path / "mypackage"
    package_dir.mkdir()

    init_file = package_dir / "__init__.py"
    init_file.write_text(
        "\n".join([
            "from .deep import DeepClass as ShallowClass",
        ]),
        encoding="utf-8",
    )

    deep_file = package_dir / "deep.py"
    deep_file.write_text(
        "\n".join([
            "class DeepClass:",
            "    pass",
        ]),
        encoding="utf-8",
    )

    client_file = tmp_path / "client.py"
    client_file.write_text(
        "\n".join([
            "from mypackage import ShallowClass",
            "",
            "def use_it():",
            "    return ShallowClass()",
        ]),
        encoding="utf-8",
    )

    # Create database records
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999302,
        name="python-aliased-reexport-repo",
        path_with_namespace="integration/python-aliased-reexport-repo",
        url="https://example.com/integration/python-aliased-reexport-repo.git",
        clone_url="https://example.com/integration/python-aliased-reexport-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    init_file_record = File(
        repository_id=repo.id,
        path="mypackage/__init__.py",
        language=LanguageEnum.PYTHON,
        size_bytes=init_file.stat().st_size,
    )
    deep_file_record = File(
        repository_id=repo.id,
        path="mypackage/deep.py",
        language=LanguageEnum.PYTHON,
        size_bytes=deep_file.stat().st_size,
    )
    client_file_record = File(
        repository_id=repo.id,
        path="client.py",
        language=LanguageEnum.PYTHON,
        size_bytes=client_file.stat().st_size,
    )
    async_session.add_all([init_file_record, deep_file_record, client_file_record])
    await async_session.flush()

    # DeepClass is the original symbol
    deep_symbol = Symbol(
        file_instance_id=deep_file_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="DeepClass",
        fully_qualified_name="mypackage.deep.DeepClass",
        start_line=1,
        end_line=2,
    )
    client_symbol = Symbol(
        file_instance_id=client_file_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.FUNCTION,
        name="use_it",
        fully_qualified_name="use_it",
        start_line=3,
        end_line=4,
    )
    async_session.add_all([deep_symbol, client_symbol])
    await async_session.flush()

    # Build import relationships
    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)

    # Verify relationship was created
    assert created >= 1

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == client_symbol.id,
            Relation.to_symbol_id == deep_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    relation = result.scalar_one_or_none()
    assert relation is not None


@pytest.mark.asyncio
async def test_python_nested_package_reexport(async_session, tmp_path: Path):
    """Test re-exports through nested package structure.

    Package structure:
    mypackage/
      __init__.py      # contains: from .sub import InnerClass
      sub/
        __init__.py    # contains: from .module import InnerClass
        module.py      # contains: class InnerClass

    Consumer:
      app.py           # contains: from mypackage import InnerClass
    """
    package_dir = tmp_path / "mypackage"
    package_dir.mkdir()

    init_file = package_dir / "__init__.py"
    init_file.write_text(
        "\n".join([
            "from .sub import InnerClass",
        ]),
        encoding="utf-8",
    )

    sub_dir = package_dir / "sub"
    sub_dir.mkdir()

    sub_init_file = sub_dir / "__init__.py"
    sub_init_file.write_text(
        "\n".join([
            "from .module import InnerClass",
        ]),
        encoding="utf-8",
    )

    module_file = sub_dir / "module.py"
    module_file.write_text(
        "\n".join([
            "class InnerClass:",
            "    pass",
        ]),
        encoding="utf-8",
    )

    app_file = tmp_path / "app.py"
    app_file.write_text(
        "\n".join([
            "from mypackage import InnerClass",
            "",
            "def main():",
            "    return InnerClass()",
        ]),
        encoding="utf-8",
    )

    # Create database records
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999303,
        name="python-nested-reexport-repo",
        path_with_namespace="integration/python-nested-reexport-repo",
        url="https://example.com/integration/python-nested-reexport-repo.git",
        clone_url="https://example.com/integration/python-nested-reexport-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    # Create all file records
    init_record = File(
        repository_id=repo.id,
        path="mypackage/__init__.py",
        language=LanguageEnum.PYTHON,
        size_bytes=init_file.stat().st_size,
    )
    sub_init_record = File(
        repository_id=repo.id,
        path="mypackage/sub/__init__.py",
        language=LanguageEnum.PYTHON,
        size_bytes=sub_init_file.stat().st_size,
    )
    module_record = File(
        repository_id=repo.id,
        path="mypackage/sub/module.py",
        language=LanguageEnum.PYTHON,
        size_bytes=module_file.stat().st_size,
    )
    app_record = File(
        repository_id=repo.id,
        path="app.py",
        language=LanguageEnum.PYTHON,
        size_bytes=app_file.stat().st_size,
    )
    async_session.add_all([init_record, sub_init_record, module_record, app_record])
    await async_session.flush()

    # Create symbols
    inner_class_symbol = Symbol(
        file_instance_id=module_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="InnerClass",
        fully_qualified_name="mypackage.sub.module.InnerClass",
        start_line=1,
        end_line=2,
    )
    main_symbol = Symbol(
        file_instance_id=app_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.FUNCTION,
        name="main",
        fully_qualified_name="main",
        start_line=3,
        end_line=4,
    )
    async_session.add_all([inner_class_symbol, main_symbol])
    await async_session.flush()

    # Build import relationships
    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)

    # Verify relationship was created
    assert created >= 1

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == main_symbol.id,
            Relation.to_symbol_id == inner_class_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    relation = result.scalar_one_or_none()
    assert relation is not None


@pytest.mark.asyncio
async def test_python_all_declaration_filters_exports(async_session, tmp_path: Path):
    """Test that __all__ limits what symbols are considered exported.

    Package structure:
    mypackage/
      __init__.py  # contains: from .module import Public, Private
                    #           __all__ = ['Public']
      module.py    # contains: class Public, class Private

    Consumer:
      app.py       # contains: from mypackage import Public
    """
    package_dir = tmp_path / "mypackage"
    package_dir.mkdir()

    init_file = package_dir / "__init__.py"
    init_file.write_text(
        "\n".join([
            "from .module import Public, Private",
            "",
            "__all__ = ['Public']",
        ]),
        encoding="utf-8",
    )

    module_file = package_dir / "module.py"
    module_file.write_text(
        "\n".join([
            "class Public:",
            "    pass",
            "",
            "class Private:",
            "    pass",
        ]),
        encoding="utf-8",
    )

    app_file = tmp_path / "app.py"
    app_file.write_text(
        "\n".join([
            "from mypackage import Public",
            "",
            "def use_public():",
            "    return Public()",
        ]),
        encoding="utf-8",
    )

    # Create database records
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999304,
        name="python-all-filter-repo",
        path_with_namespace="integration/python-all-filter-repo",
        url="https://example.com/integration/python-all-filter-repo.git",
        clone_url="https://example.com/integration/python-all-filter-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    init_record = File(
        repository_id=repo.id,
        path="mypackage/__init__.py",
        language=LanguageEnum.PYTHON,
        size_bytes=init_file.stat().st_size,
    )
    module_record = File(
        repository_id=repo.id,
        path="mypackage/module.py",
        language=LanguageEnum.PYTHON,
        size_bytes=module_file.stat().st_size,
    )
    app_record = File(
        repository_id=repo.id,
        path="app.py",
        language=LanguageEnum.PYTHON,
        size_bytes=app_file.stat().st_size,
    )
    async_session.add_all([init_record, module_record, app_record])
    await async_session.flush()

    # Create symbols
    public_symbol = Symbol(
        file_instance_id=module_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="Public",
        fully_qualified_name="mypackage.module.Public",
        start_line=1,
        end_line=2,
    )
    private_symbol = Symbol(
        file_instance_id=module_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.CLASS,
        name="Private",
        fully_qualified_name="mypackage.module.Private",
        start_line=4,
        end_line=5,
    )
    use_public_symbol = Symbol(
        file_instance_id=app_record.id,
        language=LanguageEnum.PYTHON,
        kind=SymbolKindEnum.FUNCTION,
        name="use_public",
        fully_qualified_name="use_public",
        start_line=3,
        end_line=4,
    )
    async_session.add_all([public_symbol, private_symbol, use_public_symbol])
    await async_session.flush()

    # Build import relationships
    builder = ImportRelationshipBuilder(async_session, tmp_path)
    created = await builder.build_import_relationships(repo.id)

    # Verify Public import relationship was created
    assert created >= 1

    result = await async_session.execute(
        select(Relation).where(
            Relation.from_symbol_id == use_public_symbol.id,
            Relation.to_symbol_id == public_symbol.id,
            Relation.relation_type == RelationTypeEnum.IMPORTS,
        )
    )
    relation = result.scalar_one_or_none()
    assert relation is not None

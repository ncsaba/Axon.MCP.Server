from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import LanguageEnum, RepositoryStatusEnum, SourceControlProviderEnum, SymbolKindEnum
from src.config.settings import get_settings
from src.database.models import FileInstance as File, Repository, Symbol
from src.extractors.api_extractor import ApiEndpointExtractor


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_python_fastapi_router_endpoints_are_extracted_and_saved(async_session):
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999401,
        name="python-fastapi-endpoint-repo",
        path_with_namespace="integration/python-fastapi-endpoint-repo",
        url="https://example.com/integration/python-fastapi-endpoint-repo.git",
        clone_url="https://example.com/integration/python-fastapi-endpoint-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    repo_cache_root = Path(get_settings().repo_cache_dir).resolve()
    repo_disk_path = repo_cache_root / repo.path_with_namespace.replace("/", "_")
    file_rel_path = Path("src/app/routes.py")
    file_abs_path = repo_disk_path / file_rel_path
    file_abs_path.parent.mkdir(parents=True, exist_ok=True)
    file_abs_path.write_text(
        "\n".join(
            [
                "from fastapi import APIRouter",
                "",
                "router = APIRouter(prefix=\"/api/users\")",
                "",
                "@router.get(\"/{user_id}\")",
                "async def get_user(user_id: str):",
                "    return {\"user_id\": user_id}",
            ]
        ),
        encoding="utf-8",
    )

    file = File(
        repository_id=repo.id,
        path=str(file_rel_path),
        language=LanguageEnum.PYTHON,
        size_bytes=file_abs_path.stat().st_size,
    )
    async_session.add(file)
    await async_session.flush()
    await async_session.commit()

    extractor = ApiEndpointExtractor(async_session)
    endpoints = await extractor.extract_endpoints(repo.id)

    assert any(
        ep.http_method == "GET"
        and ep.route == "/api/users/{user_id}"
        and ep.controller == "routes"
        and ep.action == "get_user"
        for ep in endpoints
    )

    saved = await extractor.save_endpoints(endpoints)
    await async_session.commit()
    assert saved >= 1

    result = await async_session.execute(
        select(Symbol).where(
            Symbol.file_instance_id == file.id,
            Symbol.kind == SymbolKindEnum.ENDPOINT,
            Symbol.name == "GET /api/users/{user_id}",
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_python_flask_blueprint_endpoints_are_extracted(async_session):
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999402,
        name="python-flask-endpoint-repo",
        path_with_namespace="integration/python-flask-endpoint-repo",
        url="https://example.com/integration/python-flask-endpoint-repo.git",
        clone_url="https://example.com/integration/python-flask-endpoint-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    repo_cache_root = Path(get_settings().repo_cache_dir).resolve()
    repo_disk_path = repo_cache_root / repo.path_with_namespace.replace("/", "_")
    file_rel_path = Path("src/app/views.py")
    file_abs_path = repo_disk_path / file_rel_path
    file_abs_path.parent.mkdir(parents=True, exist_ok=True)
    file_abs_path.write_text(
        "\n".join(
            [
                "from flask import Blueprint",
                "",
                "bp = Blueprint(\"users\", __name__, url_prefix=\"/users\")",
                "",
                "@bp.route(\"/create\", methods=[\"POST\"])",
                "def create_user():",
                "    return \"ok\"",
            ]
        ),
        encoding="utf-8",
    )

    file = File(
        repository_id=repo.id,
        path=str(file_rel_path),
        language=LanguageEnum.PYTHON,
        size_bytes=file_abs_path.stat().st_size,
    )
    async_session.add(file)
    await async_session.flush()
    await async_session.commit()

    extractor = ApiEndpointExtractor(async_session)
    endpoints = await extractor.extract_endpoints(repo.id)
    summary = {(ep.http_method, ep.route, ep.controller, ep.action) for ep in endpoints}

    assert ("POST", "/users/create", "views", "create_user") in summary

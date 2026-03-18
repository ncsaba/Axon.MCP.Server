from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import LanguageEnum, RepositoryStatusEnum, SourceControlProviderEnum, SymbolKindEnum
from src.config.settings import get_settings
from src.database.models import FileInstance as File, Repository, Symbol
from src.extractors.api_extractor import ApiEndpointExtractor


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_java_api_endpoints_are_extracted_and_saved(async_session):
    """Extract Java API endpoints from Spring/JAX-RS-style annotations.

    Temporary validation test for the Java endpoint vertical slice.
    Remove after the Java semantic extractor set is considered stable.
    """
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999004,
        name="java-endpoint-repo",
        path_with_namespace="integration/java-endpoint-repo",
        url="https://example.com/integration/java-endpoint-repo.git",
        clone_url="https://example.com/integration/java-endpoint-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    repo_cache_root = Path(get_settings().repo_cache_dir).resolve()
    repo_disk_path = repo_cache_root / repo.path_with_namespace.replace("/", "_")
    file_rel_path = Path("src/main/java/com/example/api/UserController.java")
    file_abs_path = repo_disk_path / file_rel_path
    file_abs_path.parent.mkdir(parents=True, exist_ok=True)
    file_abs_path.write_text(
        "\n".join(
            [
                "package com.example.api;",
                "@RestController",
                "@RequestMapping(\"/api/users\")",
                "public class UserController {",
                "  @GetMapping(\"/{id}\")",
                "  public User getUser(String id) { return null; }",
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
    await async_session.commit()

    extractor = ApiEndpointExtractor(async_session)
    endpoints = await extractor.extract_endpoints(repo.id)

    assert any(
        ep.http_method == "GET" and ep.route == "/api/users/{id}" and ep.controller == "UserController"
        for ep in endpoints
    )

    saved = await extractor.save_endpoints(endpoints)
    await async_session.commit()
    assert saved >= 1

    result = await async_session.execute(
        select(Symbol).where(
            Symbol.file_instance_id == file.id,
            Symbol.kind == SymbolKindEnum.ENDPOINT,
            Symbol.name == "GET /api/users/{id}",
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_java_jaxrs_api_endpoints_are_extracted(async_session):
    """Extract endpoints from JAX-RS `@Path` + verb annotations."""
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999106,
        name="java-jaxrs-endpoint-repo",
        path_with_namespace="integration/java-jaxrs-endpoint-repo",
        url="https://example.com/integration/java-jaxrs-endpoint-repo.git",
        clone_url="https://example.com/integration/java-jaxrs-endpoint-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    repo_cache_root = Path(get_settings().repo_cache_dir).resolve()
    repo_disk_path = repo_cache_root / repo.path_with_namespace.replace("/", "_")
    file_rel_path = Path("src/main/java/com/example/api/UserResource.java")
    file_abs_path = repo_disk_path / file_rel_path
    file_abs_path.parent.mkdir(parents=True, exist_ok=True)
    file_abs_path.write_text(
        "\n".join(
            [
                "package com.example.api;",
                "@Path(\"/api/users\")",
                "public class UserResource {",
                "  @GET",
                "  @Path(\"/{id}\")",
                "  public User getUser(String id) { return null; }",
                "  @POST",
                "  public User createUser(User user) { return user; }",
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
    await async_session.commit()

    extractor = ApiEndpointExtractor(async_session)
    endpoints = await extractor.extract_endpoints(repo.id)

    summary = {(ep.http_method, ep.route, ep.controller, ep.action) for ep in endpoints}
    assert ("GET", "/api/users/{id}", "UserResource", "getUser") in summary
    assert ("POST", "/api/users", "UserResource", "createUser") in summary


@pytest.mark.asyncio
async def test_java_request_mapping_multi_method_is_expanded(async_session):
    """`@RequestMapping(method={...})` should produce one endpoint per HTTP method."""
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999107,
        name="java-request-mapping-multi-method-repo",
        path_with_namespace="integration/java-request-mapping-multi-method-repo",
        url="https://example.com/integration/java-request-mapping-multi-method-repo.git",
        clone_url="https://example.com/integration/java-request-mapping-multi-method-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    repo_cache_root = Path(get_settings().repo_cache_dir).resolve()
    repo_disk_path = repo_cache_root / repo.path_with_namespace.replace("/", "_")
    file_rel_path = Path("src/main/java/com/example/api/BulkController.java")
    file_abs_path = repo_disk_path / file_rel_path
    file_abs_path.parent.mkdir(parents=True, exist_ok=True)
    file_abs_path.write_text(
        "\n".join(
            [
                "package com.example.api;",
                "@RestController",
                "@RequestMapping(\"/api\")",
                "public class BulkController {",
                "  @RequestMapping(path=\"/bulk\", method={RequestMethod.GET, RequestMethod.POST})",
                "  public String bulk() { return \"ok\"; }",
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
    await async_session.commit()

    extractor = ApiEndpointExtractor(async_session)
    endpoints = await extractor.extract_endpoints(repo.id)
    summary = {(ep.http_method, ep.route, ep.controller, ep.action) for ep in endpoints}

    assert ("GET", "/api/bulk", "BulkController", "bulk") in summary
    assert ("POST", "/api/bulk", "BulkController", "bulk") in summary

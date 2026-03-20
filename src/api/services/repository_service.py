"""Repository service utilities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.repositories import (
    RepositoryCreate,
    RepositoryRegisterFromUrl,
    RepositoryResponse,
    RepositorySyncResponse,
    GitLabProjectDiscovery,
    GitLabDiscoveryResponse,
    BulkRepositoryAddResponse,
    BulkRepositoryRemoveResponse,
    BulkRepositorySyncResponse,
    CommitInfo,
)
from src.config.enums import RepositoryStatusEnum, SourceControlProviderEnum
from src.database.models import Repository, FileInstance as File, Commit
from src.database.query_helpers import active_file_filter
from src.gitlab.client import GitLabClient
from src.gitlab.repository_manager import RepositoryManager
from src.repository_sources import is_local_directory_reference
from src.utils.logging_config import get_logger
from src.workers.tasks import sync_repository


logger = get_logger(__name__)


class RepositoryService:
    """Encapsulates repository CRUD and sync operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def build_create_payload_from_url(
        self,
        payload: RepositoryRegisterFromUrl,
    ) -> RepositoryCreate:
        """Derive repository metadata from a GitHub/generic HTTP repository URL."""
        parsed = urlparse(payload.repository_url.strip())
        path = parsed.path.strip().rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        path = path.lstrip("/")

        path_parts = [part for part in path.split("/") if part]
        if len(path_parts) < 2:
            raise ValueError("Repository URL must include an owner/group and repository name")

        path_with_namespace = "/".join(path_parts)
        name = path_parts[-1]
        provider = payload.provider or self._infer_provider_from_netloc(parsed.netloc)

        normalized_base_url = parsed._replace(params="", query="", fragment="").geturl().rstrip("/")
        if normalized_base_url.endswith(".git"):
            clone_url = normalized_base_url
            url = normalized_base_url[:-4]
        else:
            url = normalized_base_url
            clone_url = f"{normalized_base_url}.git"

        return RepositoryCreate(
            provider=provider,
            name=name,
            path_with_namespace=path_with_namespace,
            url=url,
            clone_url=clone_url,
            default_branch=payload.default_branch or "main",
        )

    def _infer_provider_from_netloc(self, netloc: str) -> SourceControlProviderEnum:
        """Infer source-control provider from the remote host."""
        lowered = netloc.lower()
        if "github.com" in lowered:
            return SourceControlProviderEnum.GITHUB
        if "gitlab" in lowered:
            return SourceControlProviderEnum.GITLAB
        return SourceControlProviderEnum.GIT

    async def _find_existing_repository(self, payload: RepositoryCreate) -> Optional[Repository]:
        """Find an existing repository using provider-specific identity rules."""
        if (
            payload.provider == SourceControlProviderEnum.GITLAB
            and payload.gitlab_project_id is not None
        ):
            stmt = select(Repository).where(
                Repository.provider == SourceControlProviderEnum.GITLAB,
                Repository.gitlab_project_id == payload.gitlab_project_id,
            )
        else:
            stmt = select(Repository).where(
                Repository.provider == payload.provider,
                Repository.path_with_namespace == payload.path_with_namespace,
            )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_existing_repository_by_url(
        self,
        payload: RepositoryRegisterFromUrl,
    ) -> Optional[Repository]:
        """Find an existing repository from a URL-derived registration payload."""
        create_payload = self.build_create_payload_from_url(payload)
        return await self._find_existing_repository(create_payload)

    def _cleanup_repository_cache(self, repository: Repository) -> bool:
        """Remove the cached git checkout for a repository when applicable."""
        clone_reference = (repository.clone_url or repository.url or "").strip()
        if is_local_directory_reference(clone_reference):
            logger.info(
                "repository_cache_cleanup_skipped_local_source",
                repository_id=repository.id,
                clone_reference=clone_reference,
            )
            return False

        manager = RepositoryManager()
        manager.cleanup_repository(repository.path_with_namespace)
        logger.info(
            "repository_cache_cleanup_completed",
            repository_id=repository.id,
            cache_key=repository.path_with_namespace,
        )
        return True

    async def list(self, *, offset: int, limit: int) -> Tuple[List[RepositoryResponse], int]:
        """List repositories with total count."""
        # Get total count
        count_stmt = select(func.count()).select_from(Repository)
        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar() or 0
        
        # Get paginated items
        stmt: Select = select(Repository).offset(offset).limit(limit).order_by(Repository.updated_at.desc())
        result = await self._session.execute(stmt)
        repositories = result.scalars().all()
        
        # Add helpful URLs and extra info to each repository
        responses = []
        for repo in repositories:
            response = RepositoryResponse.model_validate(repo)
            response.search_url = f"/api/search?repository_id={repo.id}"
            response.sync_url = f"/api/repositories/{repo.id}/sync"
            
            # Enrich with language stats and commit info
            await self._enrich_repository_response(response)
            
            responses.append(response)
        
        return responses, total

    async def get(self, repository_id: int) -> Optional[RepositoryResponse]:
        repo = await self._session.get(Repository, repository_id)
        if repo is None:
            return None
        response = RepositoryResponse.model_validate(repo)
        # Add helpful URLs
        response.search_url = f"/api/search?repository_id={repo.id}"
        response.sync_url = f"/api/repositories/{repo.id}/sync"
        
        # Enrich with language stats and commit info
        await self._enrich_repository_response(response)
        
        return response

    async def _enrich_repository_response(self, response: RepositoryResponse) -> None:
        """Populate language stats and last commit info."""
        # Get language stats
        stmt = (
            select(File.language, func.sum(File.size_bytes))
            .where(File.repository_id == response.id, active_file_filter())
            .group_by(File.language)
        )
        result = await self._session.execute(stmt)
        stats = result.all()
        
        total_size = sum((size for _, size in stats if size is not None), 0)
        if total_size > 0:
            response.languages = {lang.value: (size / total_size) * 100 for lang, size in stats if size is not None}
            # Find primary language
            if stats:
                primary = max(stats, key=lambda x: x[1] or 0)
                response.primary_language = primary[0].value
            
        # Get last commit
        stmt = select(Commit).where(Commit.repository_id == response.id).order_by(Commit.committed_date.desc()).limit(1)
        result = await self._session.execute(stmt)
        commit = result.scalar_one_or_none()
        
        if commit:
            response.last_commit = CommitInfo(
                sha=commit.sha,
                message=commit.message,
                author_name=commit.author_name,
                committed_date=commit.committed_date
            )

    async def create(self, payload: RepositoryCreate) -> RepositoryResponse:
        existing = await self._find_existing_repository(payload)
        if existing is not None:
            raise IntegrityError("Repository already exists", params=None, orig=None)

        gitlab_project_id = (
            payload.gitlab_project_id
            if payload.provider == SourceControlProviderEnum.GITLAB
            else None
        )
        repository = Repository(
            provider=payload.provider,
            gitlab_project_id=gitlab_project_id,
            name=payload.name,
            path_with_namespace=payload.path_with_namespace,
            url=payload.url,
            clone_url=payload.clone_url,
            default_branch=payload.default_branch,
            status=RepositoryStatusEnum.PENDING,
        )

        self._session.add(repository)
        try:
            await self._session.flush()
            await self._session.refresh(repository)
        except IntegrityError as exc:  # noqa: BLE001
            error_msg = f"Failed to create repository: {str(exc)}"
            logger.warning(
                "repository_create_conflict",
                provider=payload.provider,
                gitlab_project_id=payload.gitlab_project_id,
                path_with_namespace=payload.path_with_namespace,
                error=error_msg,
            )
            raise
        logger.info("repository_created", repository_id=repository.id)
        return RepositoryResponse.model_validate(repository)

    async def trigger_sync(self, repository_id: int) -> RepositorySyncResponse:
        repository = await self._session.get(Repository, repository_id)
        if repository is None:
            raise ValueError(f"Failed to trigger sync: Repository with ID {repository_id} not found")

        repository.status = RepositoryStatusEnum.PENDING
        repository.last_synced_at = datetime.now(UTC)
        await self._session.flush()

        # Trigger Celery task for background sync
        task = sync_repository.delay(repository_id)
        
        logger.info("repository_sync_enqueued", repository_id=repository_id, task_id=task.id)
        return RepositorySyncResponse(
            repository_id=repository_id,
            status="queued",
            task_id=task.id,
            message="Sync scheduled"
        )

    async def discover_gitlab_projects(self, group_id: str) -> GitLabDiscoveryResponse:
        """
        Discover all projects in a GitLab group and check tracking status.

        Args:
            group_id: GitLab group ID or path

        Returns:
            Discovery response with projects and tracking status
        """
        # Get all projects from GitLab
        gitlab_client = GitLabClient()
        gitlab_projects = gitlab_client.list_group_projects(group_id)

        # Get all tracked GitLab repositories
        stmt = select(Repository).where(Repository.provider == SourceControlProviderEnum.GITLAB)
        result = await self._session.execute(stmt)
        tracked_repos = {repo.gitlab_project_id: repo for repo in result.scalars().all()}

        # Build discovery response
        projects: List[GitLabProjectDiscovery] = []
        tracked_count = 0
        untracked_count = 0

        for project in gitlab_projects:
            gitlab_id = project["id"]
            is_tracked = gitlab_id in tracked_repos
            tracked_repo = tracked_repos.get(gitlab_id)

            if is_tracked:
                tracked_count += 1
            else:
                untracked_count += 1

            projects.append(
                GitLabProjectDiscovery(
                    gitlab_project_id=gitlab_id,
                    name=project["name"],
                    path_with_namespace=project["path_with_namespace"],
                    url=project["http_url_to_repo"],
                    default_branch=project["default_branch"],
                    description=project.get("description"),
                    visibility=project.get("visibility"),
                    is_tracked=is_tracked,
                    tracked_repository_id=tracked_repo.id if tracked_repo else None,
                )
            )

        logger.info(
            "gitlab_projects_discovered",
            group_id=group_id,
            total=len(projects),
            tracked=tracked_count,
            untracked=untracked_count,
        )

        return GitLabDiscoveryResponse(
            group_id=group_id,
            total_projects=len(projects),
            tracked_count=tracked_count,
            untracked_count=untracked_count,
            projects=projects,
        )

    async def bulk_add_repositories(
        self, repositories: List[RepositoryCreate]
    ) -> BulkRepositoryAddResponse:
        """
        Add multiple repositories in bulk.

        Args:
            repositories: List of repository creation payloads

        Returns:
            Bulk operation response
        """
        added_count = 0
        skipped_count = 0
        failed_count = 0
        added_repository_ids: List[int] = []
        errors: List[str] = []

        for repo_data in repositories:
            try:
                existing = await self._find_existing_repository(repo_data)

                if existing:
                    skipped_count += 1
                    logger.info(
                        "repository_already_exists",
                        provider=repo_data.provider,
                        gitlab_project_id=repo_data.gitlab_project_id,
                        repository_id=existing.id,
                    )
                    continue

                optimal_branch = repo_data.default_branch
                if (
                    repo_data.provider == SourceControlProviderEnum.GITLAB
                    and repo_data.gitlab_project_id is not None
                ):
                    try:
                        gitlab_client = GitLabClient()
                        optimal_branch = gitlab_client.get_optimal_branch_for_project(
                            repo_data.gitlab_project_id
                        )
                        logger.info(
                            "optimal_branch_determined",
                            provider=repo_data.provider,
                            gitlab_project_id=repo_data.gitlab_project_id,
                            optimal_branch=optimal_branch,
                            provided_branch=repo_data.default_branch
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "optimal_branch_fallback",
                            provider=repo_data.provider,
                            gitlab_project_id=repo_data.gitlab_project_id,
                            error=str(e)
                        )

                # Create new repository
                repository = Repository(
                    provider=repo_data.provider,
                    gitlab_project_id=(
                        repo_data.gitlab_project_id
                        if repo_data.provider == SourceControlProviderEnum.GITLAB
                        else None
                    ),
                    name=repo_data.name,
                    path_with_namespace=repo_data.path_with_namespace,
                    url=repo_data.url,
                    clone_url=repo_data.clone_url,
                    default_branch=optimal_branch,
                    status=RepositoryStatusEnum.PENDING,
                )

                self._session.add(repository)
                await self._session.flush()
                await self._session.refresh(repository)

                added_repository_ids.append(repository.id)
                added_count += 1

                logger.info(
                    "repository_bulk_added",
                    repository_id=repository.id,
                    provider=repo_data.provider,
                    gitlab_project_id=repo_data.gitlab_project_id,
                )

            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to add repository {repo_data.path_with_namespace}: {str(e)}"
                errors.append(error_msg)
                logger.error(
                    "repository_bulk_add_failed",
                    path_with_namespace=repo_data.path_with_namespace,
                    error=error_msg,
                )

        # Trigger Celery sync tasks for all newly added repositories
        if added_count > 0:
            for repo_id in added_repository_ids:
                try:
                    task = sync_repository.delay(repo_id)
                    logger.info(
                        "repository_sync_triggered_bulk",
                        repository_id=repo_id,
                        task_id=task.id
                    )
                except Exception as e:
                    error_msg = f"Failed to trigger sync for repository: {str(e)}"
                    logger.error(
                        "repository_sync_trigger_failed",
                        repository_id=repo_id,
                        error=error_msg
                    )

        logger.info(
            "bulk_add_completed",
            added=added_count,
            skipped=skipped_count,
            failed=failed_count,
        )

        return BulkRepositoryAddResponse(
            added_count=added_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            added_repository_ids=added_repository_ids,
            errors=errors,
        )

    async def bulk_remove_repositories(
        self,
        repository_ids: List[int],
        *,
        cleanup_cache: bool = False,
    ) -> BulkRepositoryRemoveResponse:
        """
        Remove multiple repositories in bulk.

        Args:
            repository_ids: List of repository IDs to remove

        Returns:
            Bulk operation response
        """
        removed_count = 0
        failed_count = 0
        errors: List[str] = []

        for repo_id in repository_ids:
            try:
                repository = await self._session.get(Repository, repo_id)
                if repository is None:
                    failed_count += 1
                    errors.append(f"Failed to remove repository: Repository with ID {repo_id} not found")
                    continue

                if cleanup_cache:
                    self._cleanup_repository_cache(repository)

                await self._session.delete(repository)
                removed_count += 1

                logger.info("repository_bulk_removed", repository_id=repo_id)

            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to remove repository {repo_id}: {str(e)}"
                errors.append(error_msg)
                logger.error("repository_bulk_remove_failed", repository_id=repo_id, error=error_msg)

        logger.info("bulk_remove_completed", removed=removed_count, failed=failed_count)

        return BulkRepositoryRemoveResponse(
            removed_count=removed_count, failed_count=failed_count, errors=errors
        )

    async def bulk_sync_repositories(
        self, repository_ids: List[int]
    ) -> BulkRepositorySyncResponse:
        """
        Sync multiple repositories in bulk.

        Args:
            repository_ids: List of repository IDs to sync

        Returns:
            Bulk sync response with job IDs
        """
        jobs_created = 0
        failed_count = 0
        job_ids: List[str] = []
        errors: List[str] = []

        for repo_id in repository_ids:
            try:
                repository = await self._session.get(Repository, repo_id)
                if repository is None:
                    failed_count += 1
                    errors.append(f"Failed to sync repository: Repository with ID {repo_id} not found")
                    continue

                # Update repository status and last_synced_at
                repository.status = RepositoryStatusEnum.PENDING
                repository.last_synced_at = datetime.now(UTC)
                await self._session.flush()

                # Trigger Celery task for background sync
                task = sync_repository.delay(repo_id)
                job_ids.append(task.id)
                jobs_created += 1

                logger.info(
                    "repository_sync_enqueued_bulk",
                    repository_id=repo_id,
                    task_id=task.id
                )

            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to sync repository {repo_id}: {str(e)}"
                errors.append(error_msg)
                logger.error("repository_bulk_sync_failed", repository_id=repo_id, error=error_msg)

        logger.info("bulk_sync_completed", jobs_created=jobs_created, failed=failed_count)

        return BulkRepositorySyncResponse(
            jobs_created=jobs_created,
            job_ids=job_ids,
            failed_count=failed_count,
            errors=errors,
        )

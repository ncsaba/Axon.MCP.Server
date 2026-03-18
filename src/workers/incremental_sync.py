"""Incremental repository synchronization using git diff."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

from git import Repo, GitCommandError
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Repository,
    File,
    Symbol,
    Relation,
    RepositoryIndexRun,
    Dependency,
    OutgoingApiCall,
    PublishedEvent,
    EventSubscription,
    Chunk,
)
from src.config.enums import (
    FileLifecycleStateEnum,
    LanguageEnum,
    RelationTypeEnum,
    RepositoryIndexRunStatusEnum,
    SymbolKindEnum,
)
from src.config.settings import get_settings
from src.parsers import parse_file
from src.extractors.api_extractor import ApiEndpointExtractor
from src.extractors.call_graph_builder import CallGraphBuilder
from src.extractors.config_extractor import ConfigExtractor
from src.extractors.dependency_extractor import DependencyExtractor
from src.extractors.event_extractor import EventExtractor
from src.extractors.import_resolver import ImportRelationshipBuilder
from src.extractors.knowledge_extractor import KnowledgeExtractor
from src.extractors.relationship_builder import RelationshipBuilder
from src.extractors.outgoing_call_extractor import OutgoingCallExtractor
from src.utils.logging_config import get_logger
from src.workers.file_worker import create_or_update_file
from src.workers.utils import _count_symbols
from src.workers.embedding_worker import _generate_embeddings_async

logger = get_logger(__name__)


@dataclass
class FileChange:
    """Represents a file change in git diff."""
    path: str
    change_type: str  # 'A' (added), 'M' (modified), 'D' (deleted), 'R' (renamed)
    old_path: Optional[str] = None  # For renames


class IncrementalSyncWorker:
    """Handles incremental repository synchronization."""
    
    def __init__(self, session: AsyncSession, repo_cache_dir: Path):
        """
        Initialize incremental sync worker.
        
        Args:
            session: Database session
            repo_cache_dir: Directory where repositories are cached
        """
        self.session = session
        self.repo_cache_dir = repo_cache_dir

    async def _allocate_repository_run(self, repository_id: int) -> RepositoryIndexRun:
        """Allocate a repository-scoped run for incremental git processing."""
        max_run_result = await self.session.execute(
            select(func.max(RepositoryIndexRun.run_id)).where(
                RepositoryIndexRun.repository_id == repository_id
            )
        )
        next_run_id = int(max_run_result.scalar() or 0) + 1
        run = RepositoryIndexRun(
            repository_id=repository_id,
            run_id=next_run_id,
            status=RepositoryIndexRunStatusEnum.RUNNING,
            started_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.flush()
        return run
    
    async def sync_repository_incremental(
        self,
        repository_id: int,
        repo: Repository,
        repo_path: Path
    ) -> dict:
        """
        Perform incremental sync of repository.
        
        Strategy:
        1. Get last synced commit SHA
        2. Fetch latest commit from remote
        3. If same, skip sync
        4. Get changed files using git diff
        5. Parse only changed files
        6. Update relationships for affected files
        
        Args:
            repository_id: Repository ID
            repo: Repository model
            repo_path: Path to repository on disk
            
        Returns:
            Dict with sync results
        """
        repository_run: RepositoryIndexRun | None = None
        try:
            repo = await self.session.get(Repository, repository_id)
            if repo is None:
                raise ValueError(f"Repository not found: {repository_id}")

            # Open git repository
            git_repo = Repo(repo_path)
            
            # Get latest commit
            latest_commit = git_repo.head.commit.hexsha
            last_commit = repo.last_commit_sha
            
            # Check if repository is up to date
            if last_commit == latest_commit:
                logger.info(
                    "repository_up_to_date",
                    repository_id=repository_id,
                    commit=latest_commit
                )
                return {
                    "status": "up_to_date",
                    "repository_id": repository_id,
                    "commit": latest_commit,
                    "files_changed": 0
                }
            
            logger.info(
                "repository_has_changes",
                repository_id=repository_id,
                from_commit=last_commit,
                to_commit=latest_commit
            )
            
            # Get changed files
            changed_files = self._get_changed_files(git_repo, last_commit, latest_commit)
            
            logger.info(
                "incremental_sync_detected_changes",
                repository_id=repository_id,
                files_changed=len(changed_files)
            )

            repository_run = await self._allocate_repository_run(repository_id)
            
            # Process changes
            files_processed = 0
            files_deleted = 0
            files_added = 0
            files_modified = 0
            changed_file_ids: list[int] = []
            removed_file_ids: list[int] = []
            
            for file_change in changed_files:
                if file_change.change_type == 'D':
                    removed_file_id = await self._mark_file_missing(repository_id, file_change.path)
                    if removed_file_id is not None:
                        removed_file_ids.append(removed_file_id)
                    files_deleted += 1
                elif file_change.change_type in ['A', 'M']:
                    full_path = repo_path / file_change.path
                    if full_path.exists():
                        file_record = await self._reparse_file(
                            repository_id,
                            file_change.path,
                            full_path,
                            repo_path,
                            run_id=repository_run.run_id,
                        )
                        changed_file_ids.append(file_record.id)
                        if file_change.change_type == 'A':
                            files_added += 1
                        else:
                            files_modified += 1
                        files_processed += 1
                elif file_change.change_type == 'R':
                    if file_change.old_path:
                        removed_file_id, file_record = await self._handle_rename(
                            repository_id,
                            file_change.old_path,
                            file_change.path,
                            repo_path / file_change.path,
                            repo_path,
                            run_id=repository_run.run_id,
                        )
                        if removed_file_id is not None:
                            removed_file_ids.append(removed_file_id)
                        changed_file_ids.append(file_record.id)
                        files_modified += 1
                        files_processed += 1

            if changed_file_ids or removed_file_ids:
                await self.rebuild_relationships_for_files(repository_id, changed_file_ids)
                await self._run_post_parse_parity_stages(
                    repository_id=repository_id,
                    repo_path=repo_path,
                    changed_file_ids=changed_file_ids,
                    removed_file_ids=removed_file_ids,
                )
            
            await self._update_repository_statistics(repository_id)
            repo.last_commit_sha = latest_commit
            repo.last_synced_at = datetime.now(UTC)
            repository_run.status = RepositoryIndexRunStatusEnum.SUCCEEDED
            repository_run.completed_at = datetime.now(UTC)
            await self.session.commit()
            
            logger.info(
                "incremental_sync_completed",
                repository_id=repository_id,
                files_processed=files_processed,
                files_added=files_added,
                files_modified=files_modified,
                files_deleted=files_deleted
            )
            
            return {
                "status": "success",
                "repository_id": repository_id,
                "commit": latest_commit,
                "files_changed": len(changed_files),
                "files_processed": files_processed,
                "files_added": files_added,
                "files_modified": files_modified,
                "files_deleted": files_deleted
            }
            
        except Exception as e:
            if repository_run is not None:
                try:
                    await self.session.rollback()
                    repository_run = await self.session.get(RepositoryIndexRun, repository_run.id)
                    if repository_run is not None:
                        repository_run.status = RepositoryIndexRunStatusEnum.FAILED
                        repository_run.completed_at = datetime.now(UTC)
                        repository_run.failure_reason = str(e)
                        await self.session.commit()
                except Exception:  # noqa: BLE001
                    logger.warning("incremental_sync_failed_to_record_run_failure", repository_id=repository_id)
            logger.error(
                "incremental_sync_failed",
                repository_id=repository_id,
                error=str(e),
                exc_info=True
            )
            raise
    
    def _get_changed_files(
        self,
        git_repo: Repo,
        from_commit: Optional[str],
        to_commit: str
    ) -> List[FileChange]:
        """
        Get list of changed files between commits.
        
        Args:
            git_repo: GitPython Repo object
            from_commit: Starting commit SHA (None for initial sync)
            to_commit: Ending commit SHA
            
        Returns:
            List of file changes
        """
        changes = []
        
        try:
            if not from_commit:
                # Initial sync - all files are "added"
                for item in git_repo.tree(to_commit).traverse():
                    if item.type == 'blob':  # File (not directory)
                        changes.append(FileChange(
                            path=item.path,
                            change_type='A'
                        ))
            else:
                # Get diff between commits
                diff = git_repo.commit(from_commit).diff(to_commit)
                
                # Added files
                for diff_added in diff.iter_change_type('A'):
                    changes.append(FileChange(
                        path=diff_added.b_path,
                        change_type='A'
                    ))
                
                # Modified files
                for diff_modified in diff.iter_change_type('M'):
                    changes.append(FileChange(
                        path=diff_modified.b_path,
                        change_type='M'
                    ))
                
                # Deleted files
                for diff_deleted in diff.iter_change_type('D'):
                    changes.append(FileChange(
                        path=diff_deleted.a_path,
                        change_type='D'
                    ))
                
                # Renamed files
                for diff_renamed in diff.iter_change_type('R'):
                    changes.append(FileChange(
                        path=diff_renamed.b_path,
                        change_type='R',
                        old_path=diff_renamed.a_path
                    ))
        
        except GitCommandError as e:
            logger.error("git_diff_failed", error=str(e))
            raise
        
        return changes
    
    async def _mark_file_missing(self, repository_id: int, file_path: str) -> int | None:
        """Mark a path as missing and remove active file-owned graph artifacts."""
        logger.info("marking_file_missing", repository_id=repository_id, file_path=file_path)

        result = await self.session.execute(
            select(File).where(
                File.repository_id == repository_id,
                File.path == file_path
            )
        )
        file_record = result.scalar_one_or_none()

        if not file_record:
            return None

        await self._clear_file_owned_artifacts(file_record.id)
        file_record.lifecycle_state = FileLifecycleStateEnum.MISSING
        file_record.missing_since = file_record.missing_since or datetime.now(UTC)
        file_record.last_seen_at = datetime.now(UTC)
        await self.session.flush()
        return int(file_record.id)

    async def _clear_file_owned_artifacts(self, file_id: int) -> None:
        """Clear instance-scoped extracted data before reparse or missing transition."""
        await self.session.execute(delete(Symbol).where(Symbol.file_id == file_id))
        await self.session.execute(delete(Dependency).where(Dependency.file_id == file_id))
    
    async def _reparse_file(
        self,
        repository_id: int,
        file_path: str,
        full_file_path: Path,
        repo_path: Path,
        run_id: int | None = None,
    ):
        """
        Re-parse a single file and update all related data.
        
        Args:
            repository_id: Repository ID
            file_path: Relative path within repository
            full_file_path: Full path to file on disk
        """
        logger.info("reparsing_file", repository_id=repository_id, file_path=file_path)
        
        try:
            repo = await self.session.get(Repository, repository_id)
            if repo is None:
                raise ValueError(f"Repository not found: {repository_id}")

            file_record = await create_or_update_file(
                self.session,
                repository_id,
                full_file_path,
                repo_path,
                run_id=run_id,
            )
            await self._clear_file_owned_artifacts(file_record.id)
            
            # Parse file
            parse_result = await asyncio.to_thread(parse_file, full_file_path)
            
            # Extract knowledge
            extractor = KnowledgeExtractor(self.session)
            extraction_result = await extractor.extract_and_persist(
                parse_result,
                file_record.id
            )

            await self.session.flush()
            
            logger.info(
                "file_reparsed",
                repository_id=repository_id,
                file_path=file_path,
                symbols_extracted=extraction_result.symbols_created
            )
            
            return file_record
            
        except Exception as e:
            logger.error(
                "file_reparse_failed",
                repository_id=repository_id,
                file_path=file_path,
                error=str(e),
                exc_info=True
            )
            raise
    
    async def _handle_rename(
        self,
        repository_id: int,
        old_path: str,
        new_path: str,
        full_file_path: Path,
        repo_path: Path,
        run_id: int | None = None,
    ) -> tuple[int | None, File]:
        """
        Handle file rename.
        
        Args:
            repository_id: Repository ID
            old_path: Old file path
            new_path: New file path
            full_file_path: Full path to new file
        """
        logger.info(
            "handling_file_rename",
            repository_id=repository_id,
            old_path=old_path,
            new_path=new_path
        )
        
        removed_file_id = await self._mark_file_missing(repository_id, old_path)
        file_record = await self._reparse_file(
            repository_id,
            new_path,
            full_file_path,
            repo_path,
            run_id=run_id,
        )
        return removed_file_id, file_record
    
    def _detect_language(self, file_path: Path) -> LanguageEnum:
        """Detect language from file extension."""
        suffix = file_path.suffix.lower()
        
        if suffix in ['.js', '.jsx', '.mjs']:
            return LanguageEnum.JAVASCRIPT
        elif suffix in ['.ts', '.tsx']:
            return LanguageEnum.TYPESCRIPT
        elif suffix == '.java':
            return LanguageEnum.JAVA
        elif suffix == '.vue':
            return LanguageEnum.VUE
        elif suffix == '.py':
            return LanguageEnum.PYTHON
        elif suffix in ['.md', '.markdown']:
            return LanguageEnum.MARKDOWN
        elif suffix in ['.sql', '.ddl']:
            return LanguageEnum.SQL
        elif suffix == '.json':
            # Special handling for JSON files
            filename = file_path.name.lower()
            if filename == 'package.json':
                return LanguageEnum.JAVASCRIPT  # package.json is npm/JavaScript
            return LanguageEnum.JAVASCRIPT  # Default for other JSON files
        else:
            return LanguageEnum.UNKNOWN
    
    async def rebuild_relationships_for_files(
        self,
        repository_id: int,
        file_ids: List[int]
    ):
        """
        Rebuild relationships for specific files.
        
        This is more efficient than rebuilding all relationships.
        
        Args:
            repository_id: Repository ID
            file_ids: List of file IDs that changed
        """
        logger.info(
            "rebuilding_relationships_for_files",
            repository_id=repository_id,
            file_count=len(file_ids)
        )

        relevant_relation_types = [
            RelationTypeEnum.INHERITS,
            RelationTypeEnum.IMPLEMENTS,
            RelationTypeEnum.REFERENCES,
            RelationTypeEnum.USES,
        ]

        repository_symbol_ids_result = await self.session.execute(
            select(Symbol.id).join(File).where(
                File.repository_id == repository_id,
                File.lifecycle_state == FileLifecycleStateEnum.ACTIVE,
            )
        )
        repository_symbol_ids = [int(symbol_id) for symbol_id in repository_symbol_ids_result.scalars().all()]
        if not repository_symbol_ids:
            return

        # Rebuild relationship-builder-owned relation types repository-wide to avoid duplicates.
        await self.session.execute(
            delete(Relation).where(
                Relation.relation_type.in_(relevant_relation_types),
                (Relation.from_symbol_id.in_(repository_symbol_ids)) |
                (Relation.to_symbol_id.in_(repository_symbol_ids))
            )
        )
        await self.session.commit()

        # Rebuild relationships
        relationship_builder = RelationshipBuilder(self.session)
        await relationship_builder.build_cross_file_relationships(repository_id)
        
        logger.info(
            "relationships_rebuilt",
            repository_id=repository_id,
            affected_symbols=len(repository_symbol_ids)
        )

    async def _update_repository_statistics(self, repository_id: int) -> None:
        """Refresh repository counters from active file instances after incremental sync."""
        repo = await self.session.get(Repository, repository_id)
        if repo is None:
            return

        size_result = await self.session.execute(
            select(func.sum(File.size_bytes)).where(
                File.repository_id == repository_id,
                File.lifecycle_state == FileLifecycleStateEnum.ACTIVE,
            )
        )
        total_files_result = await self.session.execute(
            select(func.count(File.id)).where(
                File.repository_id == repository_id,
                File.lifecycle_state == FileLifecycleStateEnum.ACTIVE,
            )
        )

        repo.size_bytes = int(size_result.scalar() or 0)
        repo.total_files = int(total_files_result.scalar() or 0)
        repo.total_symbols = await _count_symbols(self.session, repository_id)

    async def _run_post_parse_parity_stages(
        self,
        repository_id: int,
        repo_path: Path,
        changed_file_ids: List[int],
        removed_file_ids: List[int],
    ) -> None:
        """Run the first post-parse parity stages needed for git-sync correctness."""
        settings = get_settings()

        if settings.extract_imports:
            await self._rebuild_import_relationships(repository_id, repo_path)

        if settings.build_call_graph:
            await self._rebuild_call_graph(repository_id, changed_file_ids)

        if settings.extract_api_endpoints:
            await self._refresh_api_endpoints(repository_id)

        await self._refresh_outgoing_calls_and_events(
            repository_id=repository_id,
            repo_path=repo_path,
            changed_file_ids=changed_file_ids,
            removed_file_ids=removed_file_ids,
        )

        if settings.extract_dependencies:
            await self._refresh_dependencies(repository_id, repo_path)

        if settings.extract_configuration:
            await self._refresh_configuration(repository_id, repo_path)

        await self._refresh_embeddings(repository_id, changed_file_ids)

    async def _rebuild_import_relationships(self, repository_id: int, repo_path: Path) -> None:
        """Refresh IMPORTS edges repository-wide to match pipeline behavior."""
        repository_symbol_ids_result = await self.session.execute(
            select(Symbol.id).join(File).where(
                File.repository_id == repository_id,
                File.lifecycle_state == FileLifecycleStateEnum.ACTIVE,
            )
        )
        repository_symbol_ids = [int(symbol_id) for symbol_id in repository_symbol_ids_result.scalars().all()]
        await self.session.execute(
            delete(Relation).where(
                Relation.relation_type == RelationTypeEnum.IMPORTS,
                (Relation.from_symbol_id.in_(repository_symbol_ids)) |
                (Relation.to_symbol_id.in_(repository_symbol_ids)),
            )
        )
        await self.session.flush()

        import_builder = ImportRelationshipBuilder(self.session, repo_path)
        await import_builder.build_import_relationships(repository_id)

    async def _rebuild_call_graph(self, repository_id: int, changed_file_ids: List[int]) -> None:
        """Refresh call graph relationships for changed files."""
        if not changed_file_ids:
            return
        call_graph_builder = CallGraphBuilder(self.session)
        await call_graph_builder.build_call_relationships(
            repository_id,
            changed_file_ids=changed_file_ids,
        )

    async def _refresh_api_endpoints(self, repository_id: int) -> None:
        """Refresh generated endpoint symbols repository-wide to avoid duplicates."""
        endpoint_symbol_ids_result = await self.session.execute(
            select(Symbol.id).join(File).where(
                File.repository_id == repository_id,
                Symbol.kind == SymbolKindEnum.ENDPOINT,
            )
        )
        endpoint_symbol_ids = [int(symbol_id) for symbol_id in endpoint_symbol_ids_result.scalars().all()]
        if endpoint_symbol_ids:
            await self.session.execute(delete(Symbol).where(Symbol.id.in_(endpoint_symbol_ids)))
            await self.session.flush()

        api_extractor = ApiEndpointExtractor(self.session)
        endpoints = await api_extractor.extract_endpoints(repository_id)
        await api_extractor.save_endpoints(endpoints)
        await self.session.flush()

    async def _refresh_outgoing_calls_and_events(
        self,
        repository_id: int,
        repo_path: Path,
        changed_file_ids: List[int],
        removed_file_ids: List[int],
    ) -> None:
        """Refresh outgoing calls and events for changed files and clear stale rows for removed files."""
        touched_file_ids = sorted({int(file_id) for file_id in changed_file_ids + removed_file_ids})
        if not touched_file_ids:
            return

        await self.session.execute(delete(OutgoingApiCall).where(OutgoingApiCall.file_id.in_(touched_file_ids)))
        await self.session.execute(delete(PublishedEvent).where(PublishedEvent.file_id.in_(touched_file_ids)))
        await self.session.execute(delete(EventSubscription).where(EventSubscription.file_id.in_(touched_file_ids)))
        await self.session.flush()

        outgoing_extractor = OutgoingCallExtractor(self.session)
        event_extractor = EventExtractor(self.session)

        result = await self.session.execute(
            select(File).where(
                File.repository_id == repository_id,
                File.id.in_(changed_file_ids),
                File.lifecycle_state == FileLifecycleStateEnum.ACTIVE,
            )
        )
        changed_files = result.scalars().all()

        for file_obj in changed_files:
            file_path = repo_path / file_obj.path
            if not file_path.exists():
                continue

            try:
                content = await asyncio.to_thread(file_path.read_text, encoding="utf-8", errors="ignore")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "incremental_sync_post_parse_file_read_failed",
                    repository_id=repository_id,
                    file_path=file_obj.path,
                    error=str(exc),
                )
                continue

            for call in await outgoing_extractor.extract_from_file(file_obj, repo_path, content=content):
                self.session.add(call)

            events = await event_extractor.extract_from_file(file_obj, repo_path, content=content)
            for event in events["published"]:
                self.session.add(event)
            for subscription in events["subscribed"]:
                self.session.add(subscription)

        await self.session.flush()

    async def _refresh_dependencies(self, repository_id: int, repo_path: Path) -> None:
        """Refresh repository dependency manifests to match main pipeline behavior."""
        extractor = DependencyExtractor(self.session)
        await extractor.extract_dependencies(repository_id, repo_path)

    async def _refresh_configuration(self, repository_id: int, repo_path: Path) -> None:
        """Refresh repository configuration entries to match main pipeline behavior."""
        extractor = ConfigExtractor(self.session)
        await extractor.extract_configuration(repository_id, repo_path)

    async def _refresh_embeddings(self, repository_id: int, changed_file_ids: List[int]) -> None:
        """Generate embeddings for chunks produced by changed active files."""
        if not changed_file_ids:
            return

        chunk_ids_result = await self.session.execute(
            select(Chunk.id).where(Chunk.file_id.in_(changed_file_ids))
        )
        chunk_ids = [int(chunk_id) for chunk_id in chunk_ids_result.scalars().all()]
        if not chunk_ids:
            return

        await _generate_embeddings_async(chunk_ids)

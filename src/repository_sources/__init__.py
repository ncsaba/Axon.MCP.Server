"""Repository source abstraction for sync/index runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urlparse

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from src.gitlab.repository_manager import RepositoryManager


class RepositorySource(Protocol):
    """Runtime source contract for repository sync and path operations."""

    source_kind: str

    def sync(self, repository: Any) -> Path:
        """Prepare repository content locally and return local root path."""

    def get_repository_path(self, repository: Any) -> Path:
        """Resolve local root path for repository content."""

    def get_file_tree(self, repo_path: Path) -> List[Path]:
        """List relevant files under repository root."""

    def get_head_commit(self, repo_path: Path) -> Dict[str, Any]:
        """Return best-effort head commit metadata."""


def is_local_directory_reference(clone_url: Optional[str]) -> bool:
    """Return True when clone URL points to local filesystem path."""
    if not clone_url:
        return False
    value = clone_url.strip()
    if value.startswith("file://"):
        return True
    if value.startswith("./") or value.startswith("../"):
        return True
    return Path(value).is_absolute()


def parse_local_directory_path(clone_url: str) -> Path:
    """Parse local directory path from clone URL/path."""
    raw = clone_url.strip()
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        return Path(parsed.path).expanduser().resolve()
    return Path(raw).expanduser().resolve()


@dataclass
class GitRepositorySource:
    """Generic git-backed source."""

    source_kind: str = "git"

    def __post_init__(self) -> None:
        self._manager = RepositoryManager()

    def sync(self, repository: Any) -> Path:
        return self._manager.clone_or_update(
            repository.clone_url or repository.url,
            repository.path_with_namespace,
            repository.default_branch,
            provider=getattr(repository, "provider", None),
        )

    def get_repository_path(self, repository: Any) -> Path:
        return self._manager.cache_dir / repository.path_with_namespace.replace("/", "_").replace("\\", "_")

    def get_file_tree(self, repo_path: Path) -> List[Path]:
        return self._manager.get_file_tree(repo_path)

    def get_head_commit(self, repo_path: Path) -> Dict[str, Any]:
        return self._manager.get_head_commit(repo_path)


@dataclass
class LocalDirectorySource:
    """Direct local-directory source (no clone/update)."""

    source_kind: str = "local_directory"

    def __post_init__(self) -> None:
        self._manager = RepositoryManager()

    def sync(self, repository: Any) -> Path:
        repo_path = self.get_repository_path(repository)
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Local repository path does not exist: {repo_path}")
        return repo_path

    def get_repository_path(self, repository: Any) -> Path:
        return parse_local_directory_path(repository.clone_url or repository.url)

    def get_file_tree(self, repo_path: Path) -> List[Path]:
        return self._manager.get_file_tree(repo_path)

    def get_head_commit(self, repo_path: Path) -> Dict[str, Any]:
        try:
            repo = Repo(str(repo_path))
            commit = repo.head.commit
            parent_sha = commit.parents[0].hexsha if commit.parents else None
            return {
                "sha": commit.hexsha,
                "message": commit.message.strip(),
                "author_name": commit.author.name,
                "author_email": commit.author.email,
                "committed_date": commit.committed_datetime,
                "parent_sha": parent_sha,
            }
        except (InvalidGitRepositoryError, NoSuchPathError, ValueError):
            return {}


class RepositorySourceRegistry:
    """Resolve runtime source implementation from repository metadata."""

    def __init__(self) -> None:
        self._git = GitRepositorySource()
        self._local = LocalDirectorySource()

    def resolve(self, repository: Any) -> RepositorySource:
        if is_local_directory_reference(getattr(repository, "clone_url", None)):
            return self._local
        return self._git

    def resolve_repository_path(self, repository: Any) -> Path:
        return self.resolve(repository).get_repository_path(repository)


def get_repository_source_registry() -> RepositorySourceRegistry:
    """Factory for registry (stateless helper)."""
    return RepositorySourceRegistry()

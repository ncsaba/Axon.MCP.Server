from types import SimpleNamespace
from pathlib import Path

from src.repository_sources import (
    RepositorySourceRegistry,
    is_local_directory_reference,
    parse_local_directory_path,
)


def test_local_directory_reference_detection():
    assert is_local_directory_reference("/tmp/repo")
    assert is_local_directory_reference("./repo")
    assert is_local_directory_reference("../repo")
    assert is_local_directory_reference("file:///tmp/repo")
    assert not is_local_directory_reference("https://gitlab.example.com/group/repo.git")


def test_parse_local_directory_path_file_scheme():
    path = parse_local_directory_path("file:///tmp/repo")
    assert path == Path("/tmp/repo")


def test_registry_resolves_local_source_for_local_clone_url():
    repo = SimpleNamespace(clone_url="/tmp/repo", url="", path_with_namespace="bench/repo", default_branch="main")
    source = RepositorySourceRegistry().resolve(repo)
    assert source.source_kind == "local_directory"


def test_registry_resolves_git_source_for_remote_clone_url():
    repo = SimpleNamespace(
        clone_url="https://gitlab.example.com/group/repo.git",
        url="https://gitlab.example.com/group/repo.git",
        path_with_namespace="group/repo",
        default_branch="main",
    )
    source = RepositorySourceRegistry().resolve(repo)
    assert source.source_kind == "git"

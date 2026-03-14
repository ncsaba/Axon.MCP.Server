"""Portable file inventory provider implementations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Literal, Protocol

from src.parsers import SUPPORTED_DISCOVERY_EXTENSIONS
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_DISCOVERY_EXTENSIONS = SUPPORTED_DISCOVERY_EXTENSIONS


@dataclass(frozen=True)
class FileMeta:
    """Inventory metadata contract for one file-system entry."""

    rel_path: str
    size_bytes: int
    mtime_ns: int
    kind: Literal["file", "symlink", "other"]


class FileInventoryProvider(Protocol):
    """Provider contract for streaming file metadata."""

    directories_enumerated: int
    files_seen: int

    def stream(
        self,
        root_path: Path,
        should_include: Callable[[str, int], bool],
        should_exclude: Callable[[str], bool],
        should_exclude_directory: Callable[[str], bool] | None = None,
        on_directory_enter: Callable[[str], None] | None = None,
        on_directory_pruned: Callable[[str], None] | None = None,
    ) -> Iterator[FileMeta]:
        """Yield file metadata incrementally for root_path."""


class ScandirFileInventoryProvider:
    """Portable streaming provider backed by os.scandir."""

    def __init__(self) -> None:
        self.directories_enumerated = 0
        self.files_seen = 0

    def stream(
        self,
        root_path: Path,
        should_include: Callable[[str, int], bool],
        should_exclude: Callable[[str], bool],
        should_exclude_directory: Callable[[str], bool] | None = None,
        on_directory_enter: Callable[[str], None] | None = None,
        on_directory_pruned: Callable[[str], None] | None = None,
    ) -> Iterator[FileMeta]:
        self.directories_enumerated = 0
        self.files_seen = 0

        stack = [root_path]
        while stack:
            directory = stack.pop()
            if directory == root_path:
                rel_dir = "."
            else:
                try:
                    rel_dir = directory.relative_to(root_path).as_posix()
                except ValueError:
                    logger.warning(
                        "inventory_directory_outside_root_skipped",
                        root_path=str(root_path),
                        directory=str(directory),
                    )
                    continue

            if on_directory_enter is not None:
                on_directory_enter(rel_dir)

            self.directories_enumerated += 1

            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name)
            except OSError as exc:
                logger.warning(
                    "inventory_directory_scan_failed",
                    directory=str(directory),
                    error=str(exc),
                )
                continue

            child_dirs: list[Path] = []
            for entry in entries:
                entry_path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    try:
                        child_rel_path = entry_path.relative_to(root_path).as_posix()
                    except ValueError:
                        logger.warning(
                            "inventory_directory_outside_root_skipped",
                            root_path=str(root_path),
                            directory=str(entry_path),
                        )
                        continue
                    if should_exclude_directory is not None and should_exclude_directory(child_rel_path):
                        if on_directory_pruned is not None:
                            on_directory_pruned(child_rel_path)
                        continue
                    child_dirs.append(entry_path)
                    continue

                try:
                    rel_path = entry_path.relative_to(root_path).as_posix()
                except ValueError:
                    logger.warning(
                        "inventory_entry_outside_root_skipped",
                        root_path=str(root_path),
                        entry_path=str(entry_path),
                    )
                    continue

                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    logger.warning(
                        "inventory_entry_stat_failed",
                        entry_path=str(entry_path),
                        error=str(exc),
                    )
                    continue

                self.files_seen += 1
                size_bytes = int(stat_result.st_size)
                if should_exclude(rel_path) or not should_include(rel_path, size_bytes):
                    continue

                kind: Literal["file", "symlink", "other"]
                if entry.is_symlink():
                    kind = "symlink"
                elif entry.is_file(follow_symlinks=False):
                    kind = "file"
                else:
                    kind = "other"

                yield FileMeta(
                    rel_path=rel_path,
                    size_bytes=size_bytes,
                    mtime_ns=int(stat_result.st_mtime_ns),
                    kind=kind,
                )

            for child in reversed(child_dirs):
                stack.append(child)

"""
Git tracking models for multi-branch support.

This module contains the redesigned Commit model and related models
for tracking files across branches and commits.

Phase 2.5.1 - COMMIT Entity and Tree-Based File Tracking
"""

from datetime import UTC, datetime
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import CHAR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models_base import Base

if TYPE_CHECKING:
    from src.database.models_base import FileInstance, Repository


def utcnow() -> datetime:
    """Return current UTC datetime with timezone info."""
    return datetime.now(UTC)


class TreeType(IntEnum):
    """Type of tree entry."""

    FILE = 1
    SYMLINK = 2
    SUBMODULE = 3


class Operation(IntEnum):
    """Operation type for tree entry."""

    BASE = 0  # Base state (no change)
    ADD = 1  # File added
    MODIFY = 2  # File modified
    DELETE = 3  # File deleted


def make_flags(tree_type: TreeType, operation: Operation) -> str:
    """
    Create a flags character from tree type and operation.

    Flag encoding (4 bits):
    - Bits 0-1: tree_type (file=1, symlink=2, submodule=3)
    - Bits 2-3: operation (base=0, add=1, modify=2, delete=3)

    Common values:
    - '\\x01' (chr(1)): FILE+BASE
    - '\\x05' (chr(5)): FILE+ADD
    - '\\x09' (chr(9)): FILE+MODIFY
    - '\\x0D' (chr(13)): FILE+DELETE
    """
    return chr((operation << 2) | tree_type)


def parse_flags(flags: str) -> tuple[TreeType, Operation]:
    """
    Parse a flags character into tree type and operation.

    Args:
        flags: Single character string with encoded flags

    Returns:
        Tuple of (TreeType, Operation)
    """
    value = ord(flags)
    tree_type = TreeType(value & 0x03)  # Bits 0-1
    operation = Operation((value >> 2) & 0x03)  # Bits 2-3
    return tree_type, operation


# Pre-computed flag constants for common cases
FLAGS_FILE_BASE = make_flags(TreeType.FILE, Operation.BASE)  # chr(1)
FLAGS_FILE_ADD = make_flags(TreeType.FILE, Operation.ADD)  # chr(5)
FLAGS_FILE_MODIFY = make_flags(TreeType.FILE, Operation.MODIFY)  # chr(9)
FLAGS_FILE_DELETE = make_flags(TreeType.FILE, Operation.DELETE)  # chr(13)


class GitCommit(Base):
    """
    Redesigned Commit model with commit_sha as primary key.

    Key changes from original Commit model:
    - Uses commit_sha (VARCHAR 40) as PRIMARY KEY instead of integer id
    - repository_id is nullable (for LOCAL_STAGE commits in future phases)
    - parent_commit_sha points to NEWER commit for delta chains (inverted)
    """

    __tablename__ = "git_commits"

    commit_sha: Mapped[str] = mapped_column(String(40), primary_key=True)
    repository_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    author_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    author_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pushed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # parent_commit_sha points to the NEWER commit (child) for delta chain traversal
    # This inverted pointer allows efficient "what changed after this commit" queries
    parent_commit_sha: Mapped[Optional[str]] = mapped_column(
        String(40), ForeignKey("git_commits.commit_sha", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Relationships
    repository: Mapped[Optional["Repository"]] = relationship(
        "Repository", back_populates="git_commits"
    )
    branches: Mapped[list["Branch"]] = relationship(
        "Branch", back_populates="commit", cascade="all, delete-orphan"
    )
    tags: Mapped[list["Tag"]] = relationship(
        "Tag", back_populates="commit", cascade="all, delete-orphan"
    )
    tree_files: Mapped[list["TreeFile"]] = relationship(
        "TreeFile", back_populates="commit", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<GitCommit(commit_sha={self.commit_sha[:8]}...)>"


class TreeFile(Base):
    """
    Junction table linking commits to file instances.

    This model tracks which files exist at which commits,
    enabling multi-branch file tracking.

    The flags column uses PostgreSQL's "char" type (1 byte) with 4-bit encoding:
    - Bits 0-1: tree_type (file=1, symlink=2, submodule=3)
    - Bits 2-3: operation (base=0, add=1, modify=2, delete=3)
    """

    __tablename__ = "tree_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commit_sha: Mapped[str] = mapped_column(
        String(40), ForeignKey("git_commits.commit_sha", ondelete="CASCADE"), nullable=False
    )
    # stage_id is reserved for LOCAL_STAGE (Phase 2.5.2), default 0 for remote commits
    stage_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_instance_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("file_instances.id", ondelete="SET NULL"), nullable=True
    )
    # PostgreSQL "char" type - 1 byte storage
    flags: Mapped[str] = mapped_column(CHAR(1), nullable=False, default=FLAGS_FILE_BASE)

    # Relationships
    commit: Mapped["GitCommit"] = relationship("GitCommit", back_populates="tree_files")
    file_instance: Mapped[Optional["FileInstance"]] = relationship(
        "FileInstance", back_populates="tree_files"
    )

    def __repr__(self) -> str:
        return f"<TreeFile(commit_sha={self.commit_sha[:8]}..., file_id={self.file_instance_id})>"

    @property
    def tree_type(self) -> TreeType:
        """Get the tree type from flags."""
        return parse_flags(self.flags)[0]

    @property
    def operation(self) -> Operation:
        """Get the operation from flags."""
        return parse_flags(self.flags)[1]

    def set_flags(self, tree_type: TreeType, operation: Operation) -> None:
        """Set the flags from tree type and operation."""
        self.flags = make_flags(tree_type, operation)


class Branch(Base):
    """
    Mutable pointer to a commit.

    Branches track named references to commits within a repository.
    The is_default flag indicates the repository's default branch.
    """

    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(
        String(40), ForeignKey("git_commits.commit_sha", ondelete="CASCADE"), nullable=False
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="branches")
    commit: Mapped["GitCommit"] = relationship("GitCommit", back_populates="branches")

    __table_args__ = (
        Index("ix_branches_repository_name", "repository_id", "name", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Branch(name={self.name}, repo_id={self.repository_id})>"


class Tag(Base):
    """
    Immutable pointer to a commit.

    Tags track named references to commits within a repository.
    The message field is optional (lightweight tags have no message).
    """

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(
        String(40), ForeignKey("git_commits.commit_sha", ondelete="CASCADE"), nullable=False
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # None for lightweight tags
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="tags")
    commit: Mapped["GitCommit"] = relationship("GitCommit", back_populates="tags")

    __table_args__ = (
        Index("ix_tags_repository_name", "repository_id", "name", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Tag(name={self.name}, repo_id={self.repository_id})>"

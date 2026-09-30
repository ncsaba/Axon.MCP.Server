"""add_git_tracking_tables

Revision ID: a1b2c3d4e5f1
Revises: 3e8a6c4b2d11
Create Date: 2026-03-23 20:00:00.000000

This migration creates the git tracking tables for Phase 2.5.1:
- git_commits: Tree-based commit tracking with SHA as primary key
- tree_files: Junction table linking commits to file instances
- branches: Mutable branch pointers
- tags: Immutable tag pointers

Data migration:
- Migrates existing commits to git_commits table
- Creates tree_file records for existing file_instances
- Creates branch records for each repository's default branch

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import TIMESTAMP


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f1"
down_revision = "3e8a6c4b2d11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create git_commits table
    # Note: Using commit_sha as PRIMARY KEY (not integer id)
    # parent_commit_sha points to NEWER commit (inverted for delta chains)
    op.create_table(
        "git_commits",
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=True),
        sa.Column("author_email", sa.String(length=255), nullable=True),
        sa.Column("author_name", sa.String(length=255), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("parent_commit_sha", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("commit_sha"),
    )
    op.create_index("ix_git_commits_repository_id", "git_commits", ["repository_id"], unique=False)
    op.create_index("ix_git_commits_parent_commit_sha", "git_commits", ["parent_commit_sha"], unique=False)
    op.create_index("ix_git_commits_committed_at", "git_commits", ["committed_at"], unique=False)

    # Create branches table
    # Mutable pointer to current commit with is_default flag
    op.create_table(
        "branches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["commit_sha"], ["git_commits.commit_sha"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "name", name="uq_branches_repo_name"),
    )
    op.create_index("ix_branches_repository_id", "branches", ["repository_id"], unique=False)
    op.create_index("ix_branches_commit_sha", "branches", ["commit_sha"], unique=False)
    op.create_index("ix_branches_is_default", "branches", ["is_default"], unique=False)

    # Create tags table
    # Immutable pointer to commit with optional message for annotated tags
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["commit_sha"], ["git_commits.commit_sha"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "name", name="uq_tags_repo_name"),
    )
    op.create_index("ix_tags_repository_id", "tags", ["repository_id"], unique=False)
    op.create_index("ix_tags_commit_sha", "tags", ["commit_sha"], unique=False)

    # Create tree_files table
    # Junction table with flags encoded as "char" (1 byte PostgreSQL type)
    # Flag encoding: bits 0-1 = tree_type (1=file, 2=symlink, 3=submodule)
    #                bits 2-3 = operation (0=base, 1=add, 2=modify, 3=delete)
    op.create_table(
        "tree_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("stage_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_instance_id", sa.Integer(), nullable=True),
        sa.Column("flags", sa.String(1), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["commit_sha"], ["git_commits.commit_sha"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_instance_id"], ["file_instances.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tree_files_commit_sha", "tree_files", ["commit_sha"], unique=False)
    op.create_index("ix_tree_files_file_instance_id", "tree_files", ["file_instance_id"], unique=False)
    op.create_index("ix_tree_files_stage_id", "tree_files", ["stage_id"], unique=False)

    # ============================================================
    # DATA MIGRATION
    # ============================================================

    # Get database connection
    conn = op.get_bind()

    # Step 1: Migrate existing commits to git_commits
    # Uses 'chr(1)' for flags which represents FILE+BASE (tree_type=1, operation=0)
    conn.execute(
        text("""
        INSERT INTO git_commits (
            commit_sha,
            repository_id,
            author_email,
            author_name,
            committed_at,
            pushed_at,
            message,
            parent_commit_sha,
            created_at
        )
        SELECT
            sha AS commit_sha,
            repository_id,
            author_email,
            author_name,
            committed_date AS committed_at,
            NULL AS pushed_at,
            message,
            parent_sha AS parent_commit_sha,
            COALESCE(created_at, NOW()) AS created_at
        FROM commits
        ON CONFLICT (commit_sha) DO NOTHING
        """)
    )

    # Step 2: Create synthetic commits for repositories without commits
    # These represent the "current indexed state"
    conn.execute(
        text("""
        INSERT INTO git_commits (
            commit_sha,
            repository_id,
            author_email,
            author_name,
            committed_at,
            pushed_at,
            message,
            parent_commit_sha,
            created_at
        )
        SELECT
            COALESCE(r.last_commit_sha, '0000000000000000000000000000000000000000') AS commit_sha,
            r.id AS repository_id,
            'system' AS author_email,
            'System' AS author_name,
            r.last_synced_at AS committed_at,
            NULL AS pushed_at,
            'Synthetic commit for indexed state' AS message,
            NULL AS parent_commit_sha,
            NOW() AS created_at
        FROM repositories r
        WHERE r.last_commit_sha IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM git_commits gc WHERE gc.commit_sha = r.last_commit_sha
          )
        """)
    )

    # Step 3: Create tree_file records for existing file_instances
    # Uses chr(1) = FILE+BASE flags (tree_type=1, operation=0)
    # Links to the commit referenced by the file_instance or the repository's last commit
    conn.execute(
        text("""
        INSERT INTO tree_files (
            commit_sha,
            stage_id,
            file_instance_id,
            flags,
            created_at
        )
        SELECT
            COALESCE(
                c.sha,
                r.last_commit_sha
            ) AS commit_sha,
            0 AS stage_id,
            fi.id AS file_instance_id,
            chr(1) AS flags,
            NOW() AS created_at
        FROM file_instances fi
        JOIN repositories r ON r.id = fi.repository_id
        LEFT JOIN commits c ON c.id = fi.commit_id
        WHERE COALESCE(c.sha, r.last_commit_sha) IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM git_commits gc
              WHERE gc.commit_sha = COALESCE(c.sha, r.last_commit_sha)
          )
        """)
    )

    # Step 4: Create branch records for each repository's default branch
    # Uses repository's default_branch column or falls back to 'main'
    conn.execute(
        text("""
        INSERT INTO branches (
            repository_id,
            name,
            commit_sha,
            is_default,
            created_at,
            updated_at
        )
        SELECT
            r.id AS repository_id,
            COALESCE(r.default_branch, 'main') AS name,
            r.last_commit_sha AS commit_sha,
            true AS is_default,
            NOW() AS created_at,
            NOW() AS updated_at
        FROM repositories r
        WHERE r.last_commit_sha IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM git_commits gc WHERE gc.commit_sha = r.last_commit_sha
          )
        """)
    )


def downgrade() -> None:
    # Drop tables in reverse order (respecting foreign key constraints)
    conn = op.get_bind()

    # Clear any data that references git_tracking tables first
    # (This ensures we can cleanly drop the tables)

    # Drop tree_files table
    op.drop_index("ix_tree_files_stage_id", table_name="tree_files")
    op.drop_index("ix_tree_files_file_instance_id", table_name="tree_files")
    op.drop_index("ix_tree_files_commit_sha", table_name="tree_files")
    op.drop_table("tree_files")

    # Drop tags table
    op.drop_index("ix_tags_commit_sha", table_name="tags")
    op.drop_index("ix_tags_repository_id", table_name="tags")
    op.drop_table("tags")

    # Drop branches table
    op.drop_index("ix_branches_is_default", table_name="branches")
    op.drop_index("ix_branches_commit_sha", table_name="branches")
    op.drop_index("ix_branches_repository_id", table_name="branches")
    op.drop_table("branches")

    # Drop git_commits table
    op.drop_index("ix_git_commits_committed_at", table_name="git_commits")
    op.drop_index("ix_git_commits_parent_commit_sha", table_name="git_commits")
    op.drop_index("ix_git_commits_repository_id", table_name="git_commits")
    op.drop_table("git_commits")

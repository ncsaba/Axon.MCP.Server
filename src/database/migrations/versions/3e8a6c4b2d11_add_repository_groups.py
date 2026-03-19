"""add_repository_groups

Revision ID: 3e8a6c4b2d11
Revises: 7c9a6d5f3e21
Create Date: 2026-03-18 22:15:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "3e8a6c4b2d11"
down_revision = "7c9a6d5f3e21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repository_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("group_type", sa.String(length=50), nullable=False),
        sa.Column("inference_version", sa.String(length=50), nullable=False),
        sa.Column("group_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_repository_groups_slug"),
    )
    op.create_index(
        "ix_repository_groups_slug",
        "repository_groups",
        ["slug"],
        unique=False,
    )
    op.create_index(
        "ix_repository_groups_group_type",
        "repository_groups",
        ["group_type"],
        unique=False,
    )
    op.create_index(
        "idx_repository_group_type_slug",
        "repository_groups",
        ["group_type", "slug"],
        unique=False,
    )

    op.create_table(
        "repository_group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("membership_role", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("membership_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["repository_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "group_id",
            "repository_id",
            name="uq_repository_group_members_group_repo",
        ),
    )
    op.create_index(
        "ix_repository_group_members_group_id",
        "repository_group_members",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        "ix_repository_group_members_repository_id",
        "repository_group_members",
        ["repository_id"],
        unique=False,
    )
    op.create_index(
        "idx_repository_group_members_repo_group",
        "repository_group_members",
        ["repository_id", "group_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_repository_group_members_repo_group", table_name="repository_group_members")
    op.drop_index("ix_repository_group_members_repository_id", table_name="repository_group_members")
    op.drop_index("ix_repository_group_members_group_id", table_name="repository_group_members")
    op.drop_table("repository_group_members")

    op.drop_index("idx_repository_group_type_slug", table_name="repository_groups")
    op.drop_index("ix_repository_groups_group_type", table_name="repository_groups")
    op.drop_index("ix_repository_groups_slug", table_name="repository_groups")
    op.drop_table("repository_groups")

"""add_embeddings_hnsw_index

Revision ID: 7c9a6d5f3e21
Revises: cd4ad910d3fe
Create Date: 2026-03-18 00:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "7c9a6d5f3e21"
down_revision = "cd4ad910d3fe"
branch_labels = None
depends_on = None

FIXED_EMBEDDING_DIMENSION = 768


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM embeddings
                WHERE dimension <> {FIXED_EMBEDDING_DIMENSION}
            ) THEN
                RAISE EXCEPTION 'embeddings.dimension contains values other than {FIXED_EMBEDDING_DIMENSION}; cannot migrate to fixed vector size';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            idx_name text;
        BEGIN
            FOR idx_name IN
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'embeddings'
                  AND indexname LIKE 'embeddings_vector_d%_idx'
            LOOP
                EXECUTE format('DROP INDEX IF EXISTS %I', idx_name);
            END LOOP;
        END $$;
        """
    )
    op.execute(
        f"""
        ALTER TABLE embeddings
        ALTER COLUMN vector TYPE vector({FIXED_EMBEDDING_DIMENSION})
        USING vector::vector({FIXED_EMBEDDING_DIMENSION})
        """
    )
    op.execute(
        f"""
        ALTER TABLE embeddings
        ADD CONSTRAINT ck_embeddings_dimension_fixed
        CHECK (dimension = {FIXED_EMBEDDING_DIMENSION})
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS embeddings_vector_idx
        ON embeddings USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS embeddings_vector_idx
        """
    )
    op.execute("ALTER TABLE embeddings DROP CONSTRAINT IF EXISTS ck_embeddings_dimension_fixed")
    op.execute("ALTER TABLE embeddings ALTER COLUMN vector TYPE vector USING vector::vector")

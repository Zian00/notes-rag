"""enable pg_search extension and add BM25 index on document_chunks

Revision ID: 0007_enable_pg_search
Revises: 0006_chunk_content_hash
Create Date: 2026-07-29
"""
from alembic import op

revision = "0007_enable_pg_search"
down_revision = "0006_chunk_content_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
    # BM25 index over content only — matches what the dense embeddings represent,
    # keeping the vector and keyword retrieval paths comparable (see hybrid-search design).
    op.execute(
        "CREATE INDEX document_chunks_bm25_idx ON document_chunks "
        "USING bm25 (id, content) WITH (key_field='id')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS document_chunks_bm25_idx")
    op.execute("DROP EXTENSION IF EXISTS pg_search")

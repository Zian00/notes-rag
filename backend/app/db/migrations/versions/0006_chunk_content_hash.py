"""add content_hash to document_chunks

Revision ID: 0006_chunk_content_hash
Revises: 0005_document_status
Create Date: 2026-07-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_chunk_content_hash"
down_revision = "0005_document_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
    )
    # Backfill REAL hashes for every pre-existing row. Leaving them at the
    # server_default "" would collapse ALL of a legacy document's chunks onto a
    # single dict entry in ChunkRepository.get_hashes_for_document, so Replace's
    # chunk-diffing logic would only ever be able to delete ONE stale chunk per
    # document, permanently leaking the rest as zombie (still-searchable) rows.
    # Must run AFTER add_column (column must exist) and BEFORE create_index
    # (avoid indexing while the bulk UPDATE is rewriting every row).
    op.execute(
        "UPDATE document_chunks "
        "SET content_hash = encode(sha256(convert_to(content, 'UTF8')), 'hex')"
    )
    op.create_index("ix_document_chunks_content_hash", "document_chunks", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_content_hash", table_name="document_chunks")
    op.drop_column("document_chunks", "content_hash")

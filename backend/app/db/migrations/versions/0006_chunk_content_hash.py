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
        "document_chunks", sa.Column("content_hash", sa.String(length=64), nullable=False, server_default="")
    )
    op.create_index("ix_document_chunks_content_hash", "document_chunks", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_content_hash", table_name="document_chunks")
    op.drop_column("document_chunks", "content_hash")

"""add status/error_message to documents

Revision ID: 0005_document_status
Revises: 0004_conversations
Create Date: 2026-07-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_document_status"
down_revision = "0004_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
    )
    op.add_column("documents", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "error_message")
    op.drop_column("documents", "status")

"""create groups table, add group_id to conversations and documents, migrate course

Expand phase of the groups feature: adds the `groups` table and a nullable
`group_id` on `conversations` and `documents`, then back-fills groups from the
existing free-text `documents.course` values. The `course` column is deliberately
KEPT here — code still references it until the T4/T5 tickets land; a later
"contract" migration drops it once nothing reads it. See
docs/design/2026-08-10-groups-and-editable-chat-titles-design.md.

Revision ID: 0008_groups
Revises: 0007_enable_pg_search
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_groups"
down_revision = "0007_enable_pg_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_groups_user_id", "groups", ["user_id"])
    # Case-insensitive uniqueness per user (functional index on lower(name)).
    op.create_index(
        "uq_groups_user_lower_name", "groups", ["user_id", sa.text("lower(name)")], unique=True
    )

    op.add_column(
        "conversations",
        sa.Column(
            "group_id", sa.UUID(), sa.ForeignKey("groups.id", ondelete="SET NULL"), nullable=True
        ),
    )
    op.create_index("ix_conversations_group_id", "conversations", ["group_id"])

    op.add_column(
        "documents",
        sa.Column(
            "group_id", sa.UUID(), sa.ForeignKey("groups.id", ondelete="SET NULL"), nullable=True
        ),
    )
    op.create_index("ix_documents_group_id", "documents", ["group_id"])

    # Data migration: one group per (user, case-folded course); then assign each
    # document to its group. gen_random_uuid() is core in PostgreSQL 13+.
    op.execute(
        """
        INSERT INTO groups (id, user_id, name, created_at, updated_at)
        SELECT gen_random_uuid(), user_id, min(course), now(), now()
        FROM documents
        WHERE course IS NOT NULL AND btrim(course) <> ''
        GROUP BY user_id, lower(course)
        """
    )
    op.execute(
        """
        UPDATE documents d
        SET group_id = g.id
        FROM groups g
        WHERE g.user_id = d.user_id
          AND lower(g.name) = lower(d.course)
          AND d.course IS NOT NULL AND btrim(d.course) <> ''
        """
    )


def downgrade() -> None:
    op.drop_index("ix_documents_group_id", table_name="documents")
    op.drop_column("documents", "group_id")
    op.drop_index("ix_conversations_group_id", table_name="conversations")
    op.drop_column("conversations", "group_id")
    op.drop_index("uq_groups_user_lower_name", table_name="groups")
    op.drop_index("ix_groups_user_id", table_name="groups")
    op.drop_table("groups")

"""drop documents.course column (contract migration)

The deferred "contract" migration mentioned in 0008_groups: T5 finished
removing every code reference to the free-text `course` column (all upload/
list/replace/retrieval paths now read/write `group_id` instead), so it's safe
to drop. See docs/design/2026-08-10-groups-and-editable-chat-titles-design.md.

Revision ID: 0009_drop_course
Revises: 0008_groups
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_drop_course"
down_revision = "0008_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("documents", "course")


def downgrade() -> None:
    op.add_column("documents", sa.Column("course", sa.String(length=256), nullable=True))
    # Best-effort backfill from the current group name — group membership may
    # have changed since the column was dropped, so this recovers *a* label,
    # not necessarily the exact pre-drop value.
    op.execute(
        """
        UPDATE documents d
        SET course = g.name
        FROM groups g
        WHERE g.id = d.group_id
        """
    )

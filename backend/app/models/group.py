import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Group(Base):
    """A user-owned container that scopes both chats and documents.

    A chat inside a group retrieves only that group's documents; a document
    belongs to at most one group. Replaces the old free-text `course` field.
    """

    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Case-insensitive uniqueness per user: "CS101" and "cs101" are the same group.
    # A functional index (lower(name)) — not a plain UniqueConstraint — because the
    # rule is on the lowercased value, not the stored casing.
    __table_args__ = (
        Index("uq_groups_user_lower_name", "user_id", sa.text("lower(name)"), unique=True),
    )

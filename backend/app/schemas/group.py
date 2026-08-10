import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

# Trimmed, non-empty, max 100 chars — enforced at parse time so blank/whitespace
# names are rejected with a 422 before reaching the service.
GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class GroupCreate(BaseModel):
    name: GroupName


class GroupUpdate(BaseModel):
    name: GroupName


class GroupResponse(BaseModel):
    # from_attributes lets Pydantic read the ORM Group directly.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime


class GroupDeleteResponse(BaseModel):
    # How many chats/documents were orphaned to ungrouped by the delete.
    chats_ungrouped: int
    documents_ungrouped: int

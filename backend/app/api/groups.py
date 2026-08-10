"""Group CRUD. Deleting a group orphans its chats/documents to ungrouped (never cascades)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, get_group_service
from app.models.user import User
from app.schemas.group import GroupCreate, GroupDeleteResponse, GroupResponse, GroupUpdate
from app.services.group import GroupNameConflict, GroupNotFound, GroupService

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=GroupResponse)
async def create_group(
    body: GroupCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: GroupService = Depends(get_group_service),  # noqa: B008
) -> GroupResponse:
    # Duplicate name (case-insensitive) returns the existing group, not an error.
    group = await service.create(current_user.id, body.name)
    return GroupResponse.model_validate(group)


@router.get("", response_model=list[GroupResponse])
async def list_groups(
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: GroupService = Depends(get_group_service),  # noqa: B008
) -> list[GroupResponse]:
    groups = await service.list(current_user.id)
    return [GroupResponse.model_validate(g) for g in groups]


@router.patch("/{group_id}", response_model=GroupResponse)
async def rename_group(
    group_id: uuid.UUID,
    body: GroupUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: GroupService = Depends(get_group_service),  # noqa: B008
) -> GroupResponse:
    try:
        group = await service.rename(group_id, current_user.id, body.name)
    except GroupNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    except GroupNameConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A group with that name already exists.",
        ) from None
    return GroupResponse.model_validate(group)


@router.delete("/{group_id}", response_model=GroupDeleteResponse)
async def delete_group(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: GroupService = Depends(get_group_service),  # noqa: B008
) -> GroupDeleteResponse:
    try:
        chats, documents = await service.delete(group_id, current_user.id)
    except GroupNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    return GroupDeleteResponse(chats_ungrouped=chats, documents_ungrouped=documents)

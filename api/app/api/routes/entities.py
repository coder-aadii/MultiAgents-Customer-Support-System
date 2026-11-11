from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import ForbiddenError, NotFoundError
from app.database import get_session
from app.dependencies import get_current_user
from app.models.organization import Organization, OrgMemberRole
from app.models.user import User, UserRole
from app.schemas.entities import (
    EntityDefinition,
    OrganizationEntitySchema,
    get_default_olist_entity_schema,
)
from app.services.org_service import OrgService
from app.tenancy.rbac import ROLE_HIERARCHY

entities_router = APIRouter(prefix="/organizations", tags=["organization-entities"])


async def _resolve_and_authorize_org(
    org_id: str,
    current_user: User,
    db: AsyncSession,
    min_role: OrgMemberRole = OrgMemberRole.agent,
) -> Organization:
    target_id: uuid.UUID
    if org_id == "current":
        if not current_user.organization_id:
            raise NotFoundError("Current organization not resolved.")
        target_id = current_user.organization_id
    else:
        try:
            target_id = uuid.UUID(org_id)
        except ValueError:
            raise NotFoundError(f"Organization '{org_id}' not found.")

    stmt = select(Organization).where(Organization.id == target_id)
    org = (await db.execute(stmt)).scalar_one_or_none()
    if not org:
        raise NotFoundError(f"Organization '{org_id}' not found.")

    if current_user.role != UserRole.admin:
        membership = await OrgService.get_membership(db, org.id, current_user.id)
        if not membership:
            raise NotFoundError(f"Organization '{org_id}' not found.")
        user_level = ROLE_HIERARCHY.get(membership.role, 0)
        min_level = ROLE_HIERARCHY.get(min_role.value, 1)
        if user_level < min_level:
            raise ForbiddenError(f"Requires minimum role '{min_role.value}', but user has '{membership.role}'.")

    return org


@entities_router.get("/{org_id}/entities", response_model=OrganizationEntitySchema)
async def get_organization_entities(
    org_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> OrganizationEntitySchema:
    """Retrieve custom entity definitions and action policies for an organization."""
    org = await _resolve_and_authorize_org(org_id, current_user, db, min_role=OrgMemberRole.agent)
    raw = org.entity_schemas_json
    if not raw or not isinstance(raw, dict) or not raw.get("entities"):
        return get_default_olist_entity_schema()
    return OrganizationEntitySchema.model_validate(raw)


@entities_router.post("/{org_id}/entities", response_model=OrganizationEntitySchema, status_code=status.HTTP_201_CREATED)
async def create_or_update_entity(
    org_id: str,
    entity: EntityDefinition,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> OrganizationEntitySchema:
    """Add or update a custom entity definition on an organization."""
    org = await _resolve_and_authorize_org(org_id, current_user, db, min_role=OrgMemberRole.admin)

    raw = org.entity_schemas_json
    if not raw or not isinstance(raw, dict) or not raw.get("entities"):
        current_schema = get_default_olist_entity_schema()
    else:
        current_schema = OrganizationEntitySchema.model_validate(raw)

    existing_idx = None
    for idx, e in enumerate(current_schema.entities):
        if e.name == entity.name:
            existing_idx = idx
            break

    if existing_idx is not None:
        current_schema.entities[existing_idx] = entity
    else:
        current_schema.entities.append(entity)

    org.entity_schemas_json = current_schema.model_dump(mode="json")
    await db.commit()
    return current_schema


@entities_router.put("/{org_id}/entities", response_model=OrganizationEntitySchema)
async def set_organization_entity_schema(
    org_id: str,
    schema: OrganizationEntitySchema,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> OrganizationEntitySchema:
    """Replace full entity and action schema definition on an organization."""
    org = await _resolve_and_authorize_org(org_id, current_user, db, min_role=OrgMemberRole.admin)
    org.entity_schemas_json = schema.model_dump(mode="json")
    await db.commit()
    return schema


@entities_router.delete("/{org_id}/entities/{entity_name}", response_model=OrganizationEntitySchema)
async def delete_organization_entity(
    org_id: str,
    entity_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> OrganizationEntitySchema:
    """Delete a custom entity definition by name from an organization."""
    org = await _resolve_and_authorize_org(org_id, current_user, db, min_role=OrgMemberRole.admin)

    raw = org.entity_schemas_json
    if not raw or not isinstance(raw, dict) or not raw.get("entities"):
        current_schema = get_default_olist_entity_schema()
    else:
        current_schema = OrganizationEntitySchema.model_validate(raw)

    clean_name = entity_name.strip().lower()
    initial_count = len(current_schema.entities)
    current_schema.entities = [e for e in current_schema.entities if e.name != clean_name]
    if len(current_schema.entities) == initial_count:
        raise NotFoundError(f"Entity '{entity_name}' not found on organization.")

    org.entity_schemas_json = current_schema.model_dump(mode="json")
    await db.commit()
    return current_schema

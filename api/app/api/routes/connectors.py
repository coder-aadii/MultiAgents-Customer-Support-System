from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.credentials import CredentialManager
from app.connectors.ssrf import SSRFProtectionGate, SSRFSecurityViolationError
from app.database import get_session
from app.dependencies import require_staff
from app.models.connector_definition import ConnectorDefinition
from app.models.organization import Organization, OrganizationMembership
from app.models.user import User
from app.schemas.connector import (
    ConnectorCreate,
    ConnectorResponse,
    ConnectorTestRequest,
    ConnectorTestResponse,
    ConnectorUpdate,
)
from app.tenancy.filter import TenantQueryScoper
from app.tenancy.rbac import PERM_CONNECTORS_MANAGE, require_permission
from app.tenancy.resolver import resolve_tenant

connectors_router = APIRouter(prefix="/connectors", tags=["connectors"])


@connectors_router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    current_user: User = Depends(require_staff),
    org: Organization = Depends(resolve_tenant),
    db: AsyncSession = Depends(get_session),
) -> list[ConnectorResponse]:
    """List all configured external data connectors for the active organization."""
    stmt = select(ConnectorDefinition).where(ConnectorDefinition.organization_id == org.id)
    stmt = TenantQueryScoper.apply_scope(stmt, ConnectorDefinition)
    res = await db.execute(stmt)
    connectors = res.scalars().all()
    return [ConnectorResponse.model_validate(c) for c in connectors]


@connectors_router.post("", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    payload: ConnectorCreate,
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(require_permission(PERM_CONNECTORS_MANAGE)),
    db: AsyncSession = Depends(get_session),
) -> ConnectorResponse:
    """Register a new external connector with SSRF verification."""
    # Enforce SSRF validation on base_url for REST and GraphQL
    if payload.base_url and payload.connector_type in ("rest", "graphql"):
        try:
            SSRFProtectionGate.validate_target_url(payload.base_url)
        except SSRFSecurityViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSRF violation: {exc}",
            ) from exc

    # Check for duplicate name in tenant
    stmt = select(ConnectorDefinition).where(
        ConnectorDefinition.organization_id == org.id,
        ConnectorDefinition.name == payload.name,
    )
    stmt = TenantQueryScoper.apply_scope(stmt, ConnectorDefinition)
    res = await db.execute(stmt)
    if res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Connector with name '{payload.name}' already exists",
        )

    connector = ConnectorDefinition(
        id=uuid.uuid4(),
        organization_id=org.id,
        name=payload.name,
        connector_type=payload.connector_type,
        is_enabled=payload.is_enabled,
        base_url=payload.base_url,
        config_json=payload.config_json,
        auth_type=payload.auth_type,
        secret_reference=payload.secret_reference,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    return ConnectorResponse.model_validate(connector)


@connectors_router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector(
    connector_id: uuid.UUID,
    current_user: User = Depends(require_staff),
    org: Organization = Depends(resolve_tenant),
    db: AsyncSession = Depends(get_session),
) -> ConnectorResponse:
    """Retrieve details of a specific external connector."""
    stmt = select(ConnectorDefinition).where(
        ConnectorDefinition.id == connector_id,
        ConnectorDefinition.organization_id == org.id,
    )
    stmt = TenantQueryScoper.apply_scope(stmt, ConnectorDefinition)
    res = await db.execute(stmt)
    connector = res.scalars().first()
    if not connector:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    return ConnectorResponse.model_validate(connector)


@connectors_router.put("/{connector_id}", response_model=ConnectorResponse)
async def update_connector(
    connector_id: uuid.UUID,
    payload: ConnectorUpdate,
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(require_permission(PERM_CONNECTORS_MANAGE)),
    db: AsyncSession = Depends(get_session),
) -> ConnectorResponse:
    """Update external connector configuration with SSRF verification."""
    stmt = select(ConnectorDefinition).where(
        ConnectorDefinition.id == connector_id,
        ConnectorDefinition.organization_id == org.id,
    )
    stmt = TenantQueryScoper.apply_scope(stmt, ConnectorDefinition)
    res = await db.execute(stmt)
    connector = res.scalars().first()
    if not connector:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")

    if payload.base_url is not None:
        if payload.base_url and connector.connector_type in ("rest", "graphql"):
            try:
                SSRFProtectionGate.validate_target_url(payload.base_url)
            except SSRFSecurityViolationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"SSRF violation: {exc}",
                ) from exc
        connector.base_url = payload.base_url

    if payload.name is not None:
        connector.name = payload.name
    if payload.connector_type is not None:
        connector.connector_type = payload.connector_type
    if payload.is_enabled is not None:
        connector.is_enabled = payload.is_enabled
    if payload.config_json is not None:
        connector.config_json = payload.config_json
    if payload.auth_type is not None:
        connector.auth_type = payload.auth_type
    if payload.secret_reference is not None:
        connector.secret_reference = payload.secret_reference

    connector.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(connector)
    return ConnectorResponse.model_validate(connector)


@connectors_router.delete("/{connector_id}", status_code=status.HTTP_200_OK)
async def delete_connector(
    connector_id: uuid.UUID,
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(require_permission(PERM_CONNECTORS_MANAGE)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Delete an external connector configuration."""
    stmt = select(ConnectorDefinition).where(
        ConnectorDefinition.id == connector_id,
        ConnectorDefinition.organization_id == org.id,
    )
    stmt = TenantQueryScoper.apply_scope(stmt, ConnectorDefinition)
    res = await db.execute(stmt)
    connector = res.scalars().first()
    if not connector:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")

    await db.delete(connector)
    await db.commit()
    return {"status": "deleted", "id": str(connector_id)}


@connectors_router.post("/test-connection", response_model=ConnectorTestResponse)
async def test_connector_connection(
    payload: ConnectorTestRequest,
    org: Organization = Depends(resolve_tenant),
    _membership: OrganizationMembership = Depends(require_permission(PERM_CONNECTORS_MANAGE)),
) -> ConnectorTestResponse:
    """Validates destination against SSRF gate and attempts health ping."""
    start_time = time.perf_counter()

    # 1. SSRF gate
    try:
        SSRFProtectionGate.validate_target_url(payload.base_url)
    except SSRFSecurityViolationError as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return ConnectorTestResponse(
            success=False,
            message=f"SSRF violation: {exc}",
            latency_ms=elapsed_ms,
            error_type="SSRF_VIOLATION",
        )

    # 2. Build headers
    headers = {"Accept": "application/json"}
    if payload.auth_type != "none" and payload.secret_reference:
        try:
            secret = CredentialManager.resolve_secret(payload.secret_reference)
            if payload.auth_type == "bearer":
                headers["Authorization"] = f"Bearer {secret}"
            elif payload.auth_type == "api_key":
                headers["X-Api-Key"] = secret
            elif payload.auth_type == "basic":
                headers["Authorization"] = f"Basic {secret}"
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return ConnectorTestResponse(
                success=False,
                message=f"Secret resolution failed: {exc}",
                latency_ms=elapsed_ms,
                error_type="AUTH_ERROR",
            )

    # 3. Ping
    target_url = f"{payload.base_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get(target_url, headers=headers)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            if 200 <= res.status_code < 400:
                return ConnectorTestResponse(
                    success=True,
                    message=f"Connection successful (HTTP {res.status_code})",
                    latency_ms=elapsed_ms,
                    status_code=res.status_code,
                )
            else:
                return ConnectorTestResponse(
                    success=False,
                    message=f"Upstream returned HTTP {res.status_code}",
                    latency_ms=elapsed_ms,
                    status_code=res.status_code,
                    error_type="HTTP_ERROR",
                )
    except httpx.TimeoutException:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return ConnectorTestResponse(
            success=False,
            message="Connection timed out after 3.0s",
            latency_ms=elapsed_ms,
            error_type="TIMEOUT",
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return ConnectorTestResponse(
            success=False,
            message=f"Connection failed: {exc}",
            latency_ms=elapsed_ms,
            error_type="CONNECTION_FAILED",
        )


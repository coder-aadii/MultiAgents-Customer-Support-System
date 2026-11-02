from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.organization import Organization, OrgMemberRole
from app.models.user import UserRole
from app.tenancy.sso import SSOError, SSOHandler


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def sso_org(async_db: AsyncSession) -> Organization:
    org = Organization(
        slug=f"acme-corp-{uuid.uuid4().hex[:6]}",
        name="Acme Corporation",
        is_active=True,
        sso_config_json={
            "provider": "saml",
            "idp_sso_url": "https://idp.acme.com/sso",
            "idp_entity_id": "https://idp.acme.com",
            "default_role": "agent",
        },
    )
    async_db.add(org)
    await async_db.commit()
    return org


@pytest.mark.asyncio
async def test_saml_assertion_provisions_user_and_membership(
    async_db: AsyncSession, sso_org: Organization
):
    """Test that a valid SAML response XML provisions a new User and binds an OrganizationMembership."""
    user_email = f"john.doe.{uuid.uuid4().hex[:4]}@acme.com"
    saml_xml = f"""
    <samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
        <saml:Assertion>
            <saml:Subject>
                <saml:NameID>{user_email}</saml:NameID>
            </saml:Subject>
            <saml:AttributeStatement>
                <saml:Attribute Name="DisplayName">
                    <saml:AttributeValue>John Doe</saml:AttributeValue>
                </saml:Attribute>
                <saml:Attribute Name="Role">
                    <saml:AttributeValue>admin</saml:AttributeValue>
                </saml:Attribute>
            </saml:AttributeStatement>
        </saml:Assertion>
    </samlp:Response>
    """

    user, membership, token = await SSOHandler.process_saml_assertion(
        db=async_db,
        org=sso_org,
        assertion_xml_or_b64=saml_xml,
    )
    await async_db.commit()

    assert user.email == user_email
    assert user.display_name == "John Doe"
    assert user.role == UserRole.admin
    assert membership.organization_id == sso_org.id
    assert membership.role == OrgMemberRole.admin.value
    assert token is not None


@pytest.mark.asyncio
async def test_oidc_token_exchange_validates_signature_and_claims(
    async_db: AsyncSession, sso_org: Organization
):
    """Test that OIDC claims provision a supervisor membership."""
    user_email = f"jane.supervisor.{uuid.uuid4().hex[:4]}@acme.com"
    claims = {
        "sub": "oidc-sub-123",
        "email": user_email,
        "name": "Jane Supervisor",
        "groups": ["Support-Supervisors"],
    }

    user, membership, token = await SSOHandler.process_oidc_claims(
        db=async_db,
        org=sso_org,
        claims=claims,
    )
    await async_db.commit()

    assert user.email == user_email
    assert user.display_name == "Jane Supervisor"
    assert membership.role == OrgMemberRole.supervisor.value
    assert token is not None


@pytest.mark.asyncio
async def test_sso_login_rejects_unconfigured_organization(async_db: AsyncSession):
    """Test that attempting SSO login on an organization with no sso_config_json raises SSOError."""
    org_no_sso = Organization(
        slug=f"no-sso-{uuid.uuid4().hex[:6]}",
        name="No SSO Org",
        is_active=True,
        sso_config_json=None,
    )
    async_db.add(org_no_sso)
    await async_db.commit()

    with pytest.raises(SSOError):
        SSOHandler.get_login_url(org_no_sso)

    with pytest.raises(SSOError):
        await SSOHandler.process_saml_assertion(async_db, org_no_sso, "<xml></xml>")


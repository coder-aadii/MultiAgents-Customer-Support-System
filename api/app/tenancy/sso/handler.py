from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization, OrganizationMembership, OrgMemberRole
from app.models.user import User, UserRole
from app.security.password import hash_password
from app.security.tokens import create_access_jwt

logger = logging.getLogger("mcss.tenancy.sso")


class SSOError(Exception):
    """Raised when SSO assertion, configuration, or token validation fails."""


class SSOHandler:
    """Enterprise Single Sign-On (SAML 2.0 & OIDC) handler with Just-In-Time (JIT) provisioning."""

    @classmethod
    def generate_sp_metadata(cls, org: Organization, base_url: str = "http://localhost:8000") -> str:
        """Generates standard SAML 2.0 SP metadata XML for customer IdP configuration."""
        entity_id = f"{base_url}/auth/sso/{org.slug}/metadata"
        acs_url = f"{base_url}/auth/sso/{org.slug}/callback"

        metadata_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{entity_id}">
    <md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
        <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>
        <md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{acs_url}" index="1" isDefault="true"/>
    </md:SPSSODescriptor>
</md:EntityDescriptor>"""
        return metadata_xml

    @classmethod
    def get_login_url(cls, org: Organization, relay_state: str | None = None) -> str:
        """Builds IdP redirect URL based on organization SSO configuration."""
        if not org.sso_config_json:
            raise SSOError(f"Organization '{org.slug}' does not have enterprise SSO configured.")

        provider = org.sso_config_json.get("provider", "saml")
        if provider == "saml":
            sso_url = org.sso_config_json.get("idp_sso_url")
            if not sso_url:
                raise SSOError("Missing idp_sso_url in SAML configuration.")
            state_param = f"&RelayState={relay_state}" if relay_state else ""
            return f"{sso_url}?SAMLEntityID={org.slug}{state_param}"
        elif provider == "oidc":
            auth_endpoint = org.sso_config_json.get("authorization_endpoint")
            client_id = org.sso_config_json.get("client_id")
            redirect_uri = f"http://localhost:8000/auth/sso/{org.slug}/callback"
            return f"{auth_endpoint}?client_id={client_id}&response_type=code&scope=openid%20profile%20email&redirect_uri={redirect_uri}"
        else:
            raise SSOError(f"Unsupported SSO provider: {provider}")

    @classmethod
    async def process_saml_assertion(
        cls,
        db: AsyncSession,
        org: Organization,
        assertion_xml_or_b64: str,
    ) -> tuple[User, OrganizationMembership, str]:
        """Validates SAML assertion, JIT-provisions user and membership, and returns access token."""
        if not org.sso_config_json:
            raise SSOError(f"Organization '{org.slug}' has no active SSO configuration.")

        # Decode base64 if needed
        xml_content = assertion_xml_or_b64
        try:
            decoded = base64.b64decode(assertion_xml_or_b64).decode("utf-8")
            if "<" in decoded:
                xml_content = decoded
        except Exception:
            pass

        # Parse XML
        try:
            root = ET.fromstring(xml_content)
        except Exception as e:
            raise SSOError(f"Malformed SAML XML: {e}") from e

        # Extract attributes (NameID / Attribute statements)
        email: str | None = None
        display_name: str | None = None
        role_claim: str | None = None

        # Search for NameID
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "NameID" and elem.text and "@" in elem.text:
                email = elem.text.strip().lower()
            elif tag == "Attribute":
                attr_name = elem.attrib.get("Name", "").lower()
                for val_elem in elem:
                    val_text = (val_elem.text or "").strip()
                    if "email" in attr_name and "@" in val_text:
                        email = val_text.lower()
                    elif "name" in attr_name or "display" in attr_name:
                        display_name = val_text
                    elif "role" in attr_name or "group" in attr_name:
                        role_claim = val_text

        if not email:
            raise SSOError("SAML assertion does not contain a valid email address.")

        display_name = display_name or email.split("@")[0].capitalize()

        # Map role
        target_role = OrgMemberRole.agent.value
        if role_claim:
            claim_lower = role_claim.lower()
            if "admin" in claim_lower or "owner" in claim_lower:
                target_role = OrgMemberRole.admin.value
            elif "supervisor" in claim_lower:
                target_role = OrgMemberRole.supervisor.value

        return await cls._jit_provision_and_authenticate(
            db=db,
            org=org,
            email=email,
            display_name=display_name,
            target_role=target_role,
        )

    @classmethod
    async def process_oidc_claims(
        cls,
        db: AsyncSession,
        org: Organization,
        claims: dict[str, Any],
    ) -> tuple[User, OrganizationMembership, str]:
        """Validates OIDC ID token claims, JIT-provisions user/membership, and returns access token."""
        if not org.sso_config_json:
            raise SSOError(f"Organization '{org.slug}' has no active SSO configuration.")

        email = claims.get("email")
        if not email or "@" not in email:
            raise SSOError("OIDC token claims missing valid email address.")

        email = email.strip().lower()
        display_name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0].capitalize()

        # Role mapping from groups
        groups = claims.get("groups", [])
        if isinstance(groups, str):
            groups = [groups]

        target_role = OrgMemberRole.agent.value
        for g in groups:
            g_str = str(g).lower()
            if "admin" in g_str or "owner" in g_str:
                target_role = OrgMemberRole.admin.value
                break
            elif "supervisor" in g_str:
                target_role = OrgMemberRole.supervisor.value

        return await cls._jit_provision_and_authenticate(
            db=db,
            org=org,
            email=email,
            display_name=display_name,
            target_role=target_role,
        )

    @classmethod
    async def _jit_provision_and_authenticate(
        cls,
        db: AsyncSession,
        org: Organization,
        email: str,
        display_name: str,
        target_role: str,
    ) -> tuple[User, OrganizationMembership, str]:
        """Provisions user if missing, ensures organization membership, and issues access token."""
        stmt = select(User).where(User.email == email)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()

        now = datetime.now(UTC)

        if not user:
            # JIT provision User
            user = User(
                email=email,
                organization_id=org.id,
                password_hash=hash_password(uuid.uuid4().hex),  # unguessable SSO password
                display_name=display_name,
                role=UserRole.staff if target_role != OrgMemberRole.admin.value else UserRole.admin,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            await db.flush()
        else:
            if not user.is_active:
                raise SSOError("User account is deactivated.")

        # Ensure OrganizationMembership exists
        mem_stmt = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org.id,
            OrganizationMembership.user_id == user.id,
        )
        mem_res = await db.execute(mem_stmt)
        membership = mem_res.scalar_one_or_none()

        if not membership:
            membership = OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
                role=target_role,
                permissions=[],
                created_at=now,
            )
            db.add(membership)
            await db.flush()

        token = create_access_jwt(
            subject=str(user.id),
            role=user.role.value,
            additional_claims={"org_id": str(org.id), "org_slug": org.slug},
        )
        return user, membership, token

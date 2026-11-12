"""LLM prompt and generator for dynamic, organization-contextual customer greetings."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.models.organization import Organization
from app.providers import LLMMessage, LLMProvider, LLMRequest, get_llm_provider

logger = logging.getLogger("mcss.prompts.greeting")


def build_greeting_prompt(org: Organization | None) -> str:
    """Builds a rich system prompt contextualized with the client's business domain and entities."""
    org_name = org.name if org and org.name else "our support service"
    
    entities_desc = []
    if org and org.entity_schemas_json and isinstance(org.entity_schemas_json, dict):
        raw_ents = org.entity_schemas_json.get("entities", [])
        for ent in raw_ents:
            if isinstance(ent, dict):
                dname = ent.get("display_name") or ent.get("name")
                desc = ent.get("description", "")
                entities_desc.append(f"- {dname}: {desc}")

    entities_block = "\n".join(entities_desc) if entities_desc else "- General customer inquiries and account support"

    return f"""You are the front-line AI customer support assistant for {org_name}.
Your job is to generate a welcoming, warm, professional, and concise greeting in response to the customer.

Organization: {org_name}
Business Domain & Supported Capabilities:
{entities_block}

Guidelines:
1. Greet the customer warmly and introduce yourself on behalf of {org_name}.
2. Mention 2 to 3 key areas or services you can help with based on the supported capabilities above.
3. Keep the greeting strictly to 1 or 2 concise, clear sentences.
4. Do NOT hallucinate specific policy terms, prices, or commitments.
5. Return ONLY the plain text greeting message without any surrounding quotes, preamble, or markdown formatting.
"""


async def generate_tenant_greeting_with_llm(
    org: Organization | None,
    visitor_message: str,
    provider: LLMProvider | None = None,
    timeout_seconds: float = 3.5,
) -> str:
    """Invokes LLM with organization context to dynamically generate a tailored greeting message."""
    system_prompt = build_greeting_prompt(org)
    llm = provider or get_llm_provider()

    req = LLMRequest(
        messages=[
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=visitor_message.strip() or "Hello"),
        ],
        temperature=0.4,
        max_tokens=150,
    )

    try:
        response = await asyncio.wait_for(llm.chat_complete(req), timeout=timeout_seconds)
        content = response.content.strip().strip('"').strip("'")
        if content:
            return content
    except Exception as exc:
        logger.warning(
            "Dynamic LLM greeting generation timed out or failed (%s); falling back to default.",
            exc,
        )

    # Safe fallback if LLM times out or is unreachable
    from app.services.multi_agent_service import _build_tenant_greeting
    return _build_tenant_greeting(org)


def build_auth_required_prompt(org: Organization | None) -> str:
    """Builds a system prompt asking the LLM to guide unauthenticated users to sign in for their specific organization services."""
    org_name = org.name if org and org.name else "our support service"

    entities_desc = []
    if org and org.entity_schemas_json and isinstance(org.entity_schemas_json, dict):
        raw_ents = org.entity_schemas_json.get("entities", [])
        for ent in raw_ents:
            if isinstance(ent, dict):
                dname = ent.get("display_name") or ent.get("name")
                desc = ent.get("description", "")
                entities_desc.append(f"- {dname}: {desc}")

    entities_block = "\n".join(entities_desc) if entities_desc else "- Account records and private customer data"

    return f"""You are the front-line AI customer support assistant for {org_name}.
The customer is asking to view, manage, or inquire about their private account/records, but they are currently unauthenticated (anonymous).

Organization: {org_name}
Private Customer Services:
{entities_block}

Guidelines:
1. Politely explain that to view or manage their private records with {org_name}, they need to log in to their account first.
2. Mention that they can sign in using the 1-Click Customer Switcher or the account login on this page.
3. Warmly invite them to ask any general inquiries about {org_name} in the meantime.
4. Keep the response strictly to 1 or 2 concise, clear sentences.
5. Return ONLY the plain text response without quotes or preamble.
"""


async def generate_tenant_auth_required_with_llm(
    org: Organization | None,
    visitor_message: str,
    provider: LLMProvider | None = None,
    timeout_seconds: float = 3.5,
) -> str:
    """Invokes LLM with organization context to dynamically generate a tailored login guidance message."""
    system_prompt = build_auth_required_prompt(org)
    llm = provider or get_llm_provider()

    req = LLMRequest(
        messages=[
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=visitor_message.strip() or "account login"),
        ],
        temperature=0.4,
        max_tokens=180,
    )

    try:
        response = await asyncio.wait_for(llm.chat_complete(req), timeout=timeout_seconds)
        content = response.content.strip().strip('"').strip("'")
        if content:
            return content
    except Exception as exc:
        logger.warning(
            "Dynamic LLM auth required generation timed out or failed (%s); falling back to default.",
            exc,
        )

    # Safe fallback if LLM times out or is unreachable
    from app.services.multi_agent_service import _build_tenant_auth_required
    return _build_tenant_auth_required(org)


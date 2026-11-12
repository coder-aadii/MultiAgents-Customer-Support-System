from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.message import Message, SenderType
from app.services.context_compactor import CompactedConversationMemory, ContextAutoCompactor


def create_dummy_message(
    content: str,
    sender_type: SenderType = SenderType.visitor,
    metadata: dict | None = None,
) -> Message:
    return Message(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        sender_type=sender_type,
        content=content,
        metadata_json=metadata or {},
        created_at=datetime.now(timezone.utc),
    )


def test_short_conversation_remains_uncompacted():
    messages = [
        create_dummy_message("Hello", SenderType.visitor),
        create_dummy_message("Hi there! How can I help you?", SenderType.assistant),
        create_dummy_message("Where is my order?", SenderType.visitor),
        create_dummy_message("Could you share your order ID?", SenderType.assistant),
    ]

    memory, recent = ContextAutoCompactor.compact_conversation_history(messages)

    assert memory is None
    assert len(recent) == 4
    assert [m.content for m in recent] == [m.content for m in messages]


def test_fifty_turn_conversation_compaction_and_token_bounds():
    # Build a 50-turn synthetic conversation (25 user, 25 assistant messages)
    conv_id = uuid.uuid4()
    messages: list[Message] = []

    # Turn 1: User provides an order ID and tracking number
    messages.append(
        Message(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            sender_type=SenderType.visitor,
            content="Hi, my order ID is ORD-884920 and tracking is TRK-771920. Is it shipped?",
            metadata_json={},
            created_at=datetime.now(timezone.utc),
        )
    )
    # Turn 2: Assistant verifies order status
    messages.append(
        Message(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            sender_type=SenderType.assistant,
            content="I found your order ORD-884920. It has been shipped via standard courier.",
            metadata_json={"facts": ["Order ORD-884920 shipped on Nov 10", "Courier is DHL"]},
            created_at=datetime.now(timezone.utc),
        )
    )
    # Turn 3: User mentions workspace
    messages.append(
        Message(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            sender_type=SenderType.visitor,
            content="Also, my CloudPulse workspace is WS-prod-alpha. Can I change shipping address?",
            metadata_json={},
            created_at=datetime.now(timezone.utc),
        )
    )
    messages.append(
        Message(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            sender_type=SenderType.assistant,
            content="Address changes require verification. Your workspace WS-prod-alpha is noted.",
            metadata_json={"action": "address_change_check"},
            created_at=datetime.now(timezone.utc),
        )
    )

    # Turns 5 to 50: Continued back and forth
    for i in range(5, 51):
        if i % 2 == 1:
            messages.append(
                Message(
                    id=uuid.uuid4(),
                    conversation_id=conv_id,
                    sender_type=SenderType.visitor,
                    content=f"Turn {i}: I have another question about step {i}?",
                    metadata_json={},
                    created_at=datetime.now(timezone.utc),
                )
            )
        else:
            messages.append(
                Message(
                    id=uuid.uuid4(),
                    conversation_id=conv_id,
                    sender_type=SenderType.assistant,
                    content=f"Turn {i}: Thank you for following up, here is detailed clarification for step {i}.",
                    metadata_json={"facts": [f"Step {i} verified successfully"]},
                    created_at=datetime.now(timezone.utc),
                )
            )

    assert len(messages) == 50

    # Compact the 50-turn conversation
    memory, recent = ContextAutoCompactor.compact_conversation_history(
        messages, verbatim_recent_count=4
    )

    # Verify memory was generated
    assert memory is not None
    assert isinstance(memory, CompactedConversationMemory)
    assert memory.compacted_messages_count == 46
    assert len(recent) == 4

    # Verify entities from early turns were preserved
    assert "order_id" in memory.extracted_entities
    assert memory.extracted_entities["order_id"] == "ORD-884920"
    assert "tracking_number" in memory.extracted_entities
    assert memory.extracted_entities["tracking_number"] == "TRK-771920"
    assert "workspace_id" in memory.extracted_entities
    assert memory.extracted_entities["workspace_id"] == "WS-prod-alpha"

    # Verify established facts were captured
    assert any("DHL" in f for f in memory.verified_facts)

    # Verify recent messages remain verbatim
    assert recent[0].content == messages[46].content
    assert recent[1].content == messages[47].content
    assert recent[2].content == messages[48].content
    assert recent[3].content == messages[49].content

    # Render system context block and verify token count bounds
    context_block = memory.format_system_context_block()
    context_tokens = ContextAutoCompactor.estimate_tokens(context_block)
    assert context_tokens < 300, f"Compacted memory too large: {context_tokens} tokens"

    # Total prompt budget check: context block + recent messages
    recent_tokens = sum(ContextAutoCompactor.estimate_tokens(m.content) for m in recent)
    total_tokens = context_tokens + recent_tokens
    assert total_tokens < 2000, f"Total prompt tokens exceeded budget: {total_tokens} tokens"


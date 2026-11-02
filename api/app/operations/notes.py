from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.errors import ForbiddenError, NotFoundError
from app.models.conversation import Conversation
from app.models.conversation_note import ConversationInternalNote
from app.models.user import User, UserRole
from app.tenancy.filter import TenantQueryScoper


class InternalNotesService:
    """Manages internal staff collaboration notes and private supervisor whisper coaching."""

    @classmethod
    async def add_note(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        author_user: User,
        note_text: str,
        is_whisper: bool = False,
    ) -> ConversationInternalNote:
        """Adds a private internal note or supervisor whisper to a conversation."""
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_stmt = TenantQueryScoper.apply_scope(conv_stmt, Conversation, org_id=author_user.organization_id)
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if not conv or conv.organization_id != author_user.organization_id:
            raise NotFoundError("Conversation not found")

        # Whisper mode requires supervisor or admin privilege
        if is_whisper and author_user.role not in (UserRole.admin, UserRole.staff):
            raise ForbiddenError("Whisper mode requires staff or supervisor privilege")

        note = ConversationInternalNote(
            organization_id=conv.organization_id,
            conversation_id=conversation_id,
            author_user_id=author_user.id,
            note_text=note_text.strip(),
            is_whisper=is_whisper,
            created_at=datetime.now(UTC),
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)
        return note

    @classmethod
    async def list_notes(
        cls,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        requesting_user: User,
    ) -> list[ConversationInternalNote]:
        """Lists notes, filtering supervisor whispers to assigned agent, author, or admins."""
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_stmt = TenantQueryScoper.apply_scope(conv_stmt, Conversation, org_id=requesting_user.organization_id)
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if not conv or conv.organization_id != requesting_user.organization_id:
            raise NotFoundError("Conversation not found")

        stmt = (
            select(ConversationInternalNote)
            .where(ConversationInternalNote.conversation_id == conversation_id)
            .order_by(ConversationInternalNote.created_at.asc())
        )
        stmt = TenantQueryScoper.apply_scope(stmt, ConversationInternalNote, org_id=requesting_user.organization_id)
        res = await db.execute(stmt)
        all_notes = list(res.scalars().all())

        visible_notes: list[ConversationInternalNote] = []
        is_admin = requesting_user.role == UserRole.admin
        is_assigned_agent = conv.assigned_user_id == requesting_user.id or conv.assigned_agent_id == requesting_user.id

        for note in all_notes:
            if not note.is_whisper:
                visible_notes.append(note)
            else:
                # Whisper note: visible only to author, assigned agent, or admin
                if is_admin or is_assigned_agent or note.author_user_id == requesting_user.id:
                    visible_notes.append(note)

        return visible_notes


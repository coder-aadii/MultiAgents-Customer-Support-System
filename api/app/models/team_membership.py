from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.support_team import SupportTeam
    from app.models.user import User


class TeamMembership(Base):
    __tablename__ = "team_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support_teams.id", ondelete="CASCADE", name="fk_team_memberships_team_id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_team_memberships_user_id"),
        nullable=False,
    )
    is_team_lead: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_concurrent_chats: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    skills: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    team: Mapped[SupportTeam] = relationship("SupportTeam", back_populates="memberships")
    user: Mapped[User] = relationship("User")

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
    )


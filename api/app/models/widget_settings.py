import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WidgetSettings(Base):
    __tablename__ = "widget_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    allowed_origin: Mapped[str] = mapped_column(String(2048), nullable=False)
    welcome_message: Mapped[str] = mapped_column(Text, nullable=False)
    accent_color: Mapped[str] = mapped_column(String(7), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<WidgetSettings(id={self.id})>"

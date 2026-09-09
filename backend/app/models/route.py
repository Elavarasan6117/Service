from __future__ import annotations

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EntityStatus
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Route(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "routes"

    route_code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    route_name: Mapped[str] = mapped_column(String(255), nullable=False)
    service_area: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[EntityStatus] = mapped_column(
        Enum(EntityStatus, name="entity_status", native_enum=False, length=32),
        nullable=False,
        default=EntityStatus.ACTIVE,
    )

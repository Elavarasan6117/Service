from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ConfigValueType
from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AppConfig(TimestampMixin, Base):
    """Runtime business configuration.

    The serviceability threshold lives here rather than in an environment
    variable so that changing it is an audited administrative action, not a
    redeploy. Values are read fresh on each use.
    """

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[ConfigValueType] = mapped_column(
        Enum(ConfigValueType, name="config_value_type", native_enum=False, length=16),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    # When true, only ADMIN may change it. The threshold is the reason this
    # column exists (brief §21).
    requires_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    min_value: Mapped[str | None] = mapped_column(String(64))
    max_value: Mapped[str | None] = mapped_column(String(64))


class ConfigAudit(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only history of configuration changes."""

    __tablename__ = "config_audit"

    config_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    changed_by_label: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

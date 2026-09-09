from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import Permission, UserRole, role_has
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False, length=32), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Brute-force protection state, persisted so it survives a restart and is
    # shared across API replicas.
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def has(self, permission: Permission) -> bool:
        return self.is_active and role_has(self.role, permission)

    @property
    def label(self) -> str:
        """Denormalised actor label stored on audit rows.

        Audit records keep this alongside the user id so that a decision remains
        explainable even if the user is later renamed or removed.
        """
        return f"{self.full_name} ({self.username})"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} {self.role}>"

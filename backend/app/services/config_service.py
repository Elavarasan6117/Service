"""Runtime business configuration, read from the database.

The serviceability threshold is the reason this module exists. It must be:
  * changeable without a redeploy,
  * changeable only by an administrator,
  * audited on every change,
  * consistent across API replicas.

That rules out an environment variable and rules out a process-level cache, so
values are read fresh from the database on each use. The reads are trivially
cheap (single-row primary-key lookups on a table with a handful of rows).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ConfigValueType, Permission
from app.core.errors import ConfigurationError, ForbiddenError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.config import AppConfig, ConfigAudit
from app.models.user import User

logger = get_logger(__name__)

# Canonical keys. Referenced by name nowhere else as a bare string.
KEY_THRESHOLD = "SERVICEABILITY_RADIUS_METERS"
KEY_CANDIDATE_FACTOR = "CANDIDATE_RADIUS_FACTOR"
KEY_CANDIDATE_MAX = "CANDIDATE_MAX_COUNT"
KEY_CACHE_TTL = "ROUTE_CACHE_TTL_SECONDS"
KEY_EMPTY_TABLE_RESULT = "SERVICEABILITY_EMPTY_TABLE_RESULT"
KEY_AUTO_CHECK_ON_CREATE = "AUTO_CHECK_ON_CUSTOMER_CREATE"
KEY_DECISION_ENABLED = "AUTOMATIC_DECISION_ENABLED"


class ConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- reads -----------------------------------------------------------

    async def _row(self, key: str) -> AppConfig:
        row = await self.session.get(AppConfig, key)
        if row is None:
            raise ConfigurationError(
                f"Configuration key '{key}' is missing. "
                "Run database migrations to seed default configuration."
            )
        return row

    @staticmethod
    def _coerce(row: AppConfig) -> Any:
        match row.value_type:
            case ConfigValueType.INT:
                return int(row.value)
            case ConfigValueType.FLOAT:
                return float(row.value)
            case ConfigValueType.BOOL:
                return row.value.strip().lower() in ("1", "true", "yes", "on")
            case ConfigValueType.JSON:
                return json.loads(row.value)
            case _:
                return row.value

    async def get(self, key: str) -> Any:
        return self._coerce(await self._row(key))

    async def get_int(self, key: str) -> int:
        return int(await self.get(key))

    async def get_float(self, key: str) -> float:
        return float(await self.get(key))

    async def get_bool(self, key: str) -> bool:
        return bool(await self.get(key))

    async def get_threshold_meters(
        self, service_type: str | None = None, area: str | None = None
    ) -> int:
        """The serviceability threshold, in metres.

        ``service_type`` and ``area`` are accepted but not yet used. They are in
        the signature today so that adding per-type or per-area thresholds
        (brief §32 items 12-13) is a change to this method alone, with no call
        site in the engine to touch.
        """
        value = await self.get_int(KEY_THRESHOLD)
        if value <= 0:
            raise ConfigurationError(
                f"{KEY_THRESHOLD} is {value}; it must be greater than zero."
            )
        return value

    async def all(self) -> list[AppConfig]:
        result = await self.session.execute(select(AppConfig).order_by(AppConfig.key))
        return list(result.scalars().all())

    # -- writes ----------------------------------------------------------

    async def set(
        self, key: str, new_value: str, *, actor: User, reason: str
    ) -> AppConfig:
        row = await self._row(key)

        if not reason or not reason.strip():
            # A threshold change with no stated reason is unauditable in
            # practice, so the reason is mandatory rather than optional.
            raise ValidationError("A reason is required for every configuration change.")

        required = (
            Permission.CONFIG_WRITE_THRESHOLD
            if row.requires_admin
            else Permission.CONFIG_WRITE_GENERAL
        )
        if not actor.has(required):
            raise ForbiddenError(
                f"Changing '{key}' requires the {required.value} permission."
            )

        self._validate_value(row, new_value)

        old_value = row.value
        if old_value == new_value:
            return row

        row.value = new_value
        self.session.add(
            ConfigAudit(
                config_key=key,
                old_value=old_value,
                new_value=new_value,
                changed_by=actor.id,
                changed_by_label=actor.label,
                reason=reason.strip(),
            )
        )
        await self.session.flush()

        logger.info(
            "config_changed",
            key=key,
            old_value=old_value,
            new_value=new_value,
            actor=actor.username,
            reason=reason.strip(),
        )
        return row

    @staticmethod
    def _validate_value(row: AppConfig, value: str) -> None:
        try:
            match row.value_type:
                case ConfigValueType.INT:
                    parsed: Any = int(value)
                case ConfigValueType.FLOAT:
                    parsed = float(value)
                case ConfigValueType.BOOL:
                    if value.strip().lower() not in (
                        "true", "false", "1", "0", "yes", "no", "on", "off",
                    ):
                        raise ValueError("not a boolean")
                    parsed = None
                case ConfigValueType.JSON:
                    json.loads(value)
                    parsed = None
                case _:
                    parsed = None
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                f"'{value}' is not a valid {row.value_type.value} for {row.key}."
            ) from exc

        if parsed is not None and isinstance(parsed, (int, float)):
            if row.min_value is not None and parsed < float(row.min_value):
                raise ValidationError(
                    f"{row.key} must be at least {row.min_value}."
                )
            if row.max_value is not None and parsed > float(row.max_value):
                raise ValidationError(f"{row.key} must be at most {row.max_value}.")

    async def history(self, key: str, limit: int = 100) -> list[ConfigAudit]:
        await self._row(key)  # 404 if the key does not exist
        result = await self.session.execute(
            select(ConfigAudit)
            .where(ConfigAudit.config_key == key)
            .order_by(ConfigAudit.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def require_exists(self, key: str) -> AppConfig:
        try:
            return await self._row(key)
        except ConfigurationError as exc:
            raise NotFoundError(f"No configuration key named '{key}'.") from exc

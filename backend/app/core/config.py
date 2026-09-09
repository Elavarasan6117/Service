"""Application settings.

Everything here comes from the environment. Nothing sensitive has a usable
default -- a missing SECRET_KEY in production is a startup failure, not a
silent fallback to something guessable.

Note the distinction between *infrastructure* settings (here) and *business*
settings such as the serviceability threshold (in the ``app_config`` table).
Business values must be changeable by an administrator at runtime with an
audit trail; they must never require a redeploy. The DEFAULT_* values below
are used exactly once, to seed the database, and are ignored thereafter.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "qa", "uat", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Application ---------------------------------------------------
    ENVIRONMENT: Environment = "development"
    DEBUG: bool = False
    APP_NAME: str = "Chennai Serviceability Operations"
    API_V1_PREFIX: str = "/api/v1"
    ENABLE_API_DOCS: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"
    TIMEZONE: str = "Asia/Kolkata"

    # ---- Security ------------------------------------------------------
    SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(64))
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    BCRYPT_ROUNDS: int = 12
    CORS_ORIGINS: str = "http://localhost:5173"
    TRUSTED_HOSTS: str = "localhost,127.0.0.1"
    FORCE_HTTPS: bool = False

    BOOTSTRAP_ADMIN_USERNAME: str = "admin"
    BOOTSTRAP_ADMIN_EMAIL: str = "ops@example.com"
    BOOTSTRAP_ADMIN_PASSWORD: str = ""

    # ---- Database ------------------------------------------------------
    POSTGRES_USER: str = "serviceability"
    POSTGRES_PASSWORD: str = "serviceability"
    POSTGRES_DB: str = "serviceability"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False
    DATABASE_URL_OVERRIDE: str | None = None

    # ---- Redis ---------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"

    # ---- Providers -----------------------------------------------------
    ROUTING_PROVIDER: str = "google_maps"
    GEOCODING_PROVIDER: str = "google_maps"
    ROUTING_FALLBACK_PROVIDER: str = ""

    GOOGLE_MAPS_API_KEY: str = ""
    GOOGLE_MAPS_REGION: str = ""
    GOOGLE_MAPS_LANGUAGE: str = "en"
    GOOGLE_PLACES_COUNTRY_FILTER: str = ""
    # Shared by the OSM-based providers (nominatim, photon): place names come
    # back in this language where a translation/transliteration exists, e.g.
    # "Tokyo" rather than the Japanese script form, instead of whatever the
    # local script for that country happens to be.
    GEOCODING_LANGUAGE: str = "en"
    NOMINATIM_BASE_URL: str = "https://nominatim.openstreetmap.org"
    NOMINATIM_USER_AGENT: str = "ChennaiServiceability/1.0 local development"
    NOMINATIM_COUNTRY_CODES: str = ""
    # Degrees of latitude/longitude padding around MAP_DEFAULT_CENTER_* used to
    # bias (not restrict) Nominatim search results toward Chennai. ~0.6 degrees
    # covers the city and its exurbs. Without this, a query that doesn't match
    # cleanly (see the fallback prefixes in NominatimProvider.geocode) can
    # resolve to a same-named street or locality elsewhere in the world ahead
    # of the correct nearby one.
    NOMINATIM_VIEWBOX_DEGREES: float = 0.6
    # Komoot's public Photon instance: same OSM data as Nominatim, but built
    # for application traffic (no key, generous rate limit) rather than the
    # osm.org Nominatim endpoint's "light manual use only" policy.
    PHOTON_BASE_URL: str = "https://photon.komoot.io"

    OSRM_BASE_URL: str = "http://localhost:5000"

    PROVIDER_TIMEOUT_SECONDS: float = 8.0
    PROVIDER_MAX_RETRIES: int = 3
    PROVIDER_BACKOFF_BASE_SECONDS: float = 0.5
    PROVIDER_CIRCUIT_FAIL_THRESHOLD: int = 5
    PROVIDER_CIRCUIT_RESET_SECONDS: int = 60

    # ---- Business defaults (seed only -- DB is the source of truth) -----
    DEFAULT_SERVICEABILITY_RADIUS_METERS: int = 2000
    DEFAULT_CANDIDATE_RADIUS_FACTOR: float = 4.0
    DEFAULT_CANDIDATE_MAX_COUNT: int = 10
    DEFAULT_ROUTE_CACHE_TTL_SECONDS: int = 86400

    # ---- Map -----------------------------------------------------------
    MAP_DEFAULT_CENTER_LAT: float = 13.0827
    MAP_DEFAULT_CENTER_LNG: float = 80.2707
    MAP_DEFAULT_ZOOM: int = 12
    MAP_TILE_URL: str = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
    MAP_TILE_ATTRIBUTION: str = "&copy; OpenStreetMap contributors"

    # ---- Rate limiting -------------------------------------------------
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 300
    RATE_LIMIT_CHECK_PER_MINUTE: int = 60
    RATE_LIMIT_AUTOCOMPLETE_PER_MINUTE: int = 120
    RATE_LIMIT_LOGIN_PER_15MIN: int = 5

    # ---- Observability -------------------------------------------------
    METRICS_ENABLED: bool = True
    SENTRY_DSN: str = ""

    # ---- Import validation ---------------------------------------------
    IMPORT_BBOX_ENABLED: bool = True
    IMPORT_BBOX_MIN_LAT: float = 12.6
    IMPORT_BBOX_MAX_LAT: float = 13.6
    IMPORT_BBOX_MIN_LNG: float = 79.9
    IMPORT_BBOX_MAX_LNG: float = 80.6
    IMPORT_MAX_ROWS: int = 50_000

    # ---- Derived -------------------------------------------------------
    @property
    def database_url(self) -> str:
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        """Alembic and management scripts use the sync driver."""
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE.replace("+asyncpg", "+psycopg2")
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [h.strip() for h in self.TRUSTED_HOSTS.split(",") if h.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    # ---- Validation ----------------------------------------------------
    @field_validator("DEFAULT_SERVICEABILITY_RADIUS_METERS")
    @classmethod
    def _positive_threshold(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("DEFAULT_SERVICEABILITY_RADIUS_METERS must be > 0")
        return v

    @field_validator("DEFAULT_CANDIDATE_RADIUS_FACTOR")
    @classmethod
    def _factor_at_least_one(cls, v: float) -> float:
        # Below 1.0 the Stage-1 filter could discard a location that is within
        # the threshold by road, which would make the decision wrong.
        if v < 1.0:
            raise ValueError(
                "DEFAULT_CANDIDATE_RADIUS_FACTOR must be >= 1.0; a smaller value "
                "can exclude service locations that are within the threshold by road."
            )
        return v

    @model_validator(mode="after")
    def _production_hardening(self) -> "Settings":
        """Refuse to start production with development-grade configuration."""
        if not self.is_production:
            return self

        problems: list[str] = []
        if len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY must be at least 32 characters in production")
        if self.DEBUG:
            problems.append("DEBUG must be false in production")
        if self.ENABLE_API_DOCS:
            problems.append("ENABLE_API_DOCS must be false in production")
        if not self.FORCE_HTTPS:
            problems.append("FORCE_HTTPS must be true in production")
        if self.ROUTING_PROVIDER == "fake" or self.GEOCODING_PROVIDER == "fake":
            problems.append("The 'fake' provider must never be used in production")
        if self.ROUTING_PROVIDER == "google_maps" and not self.GOOGLE_MAPS_API_KEY:
            problems.append("GOOGLE_MAPS_API_KEY is required when using google_maps")
        if self.POSTGRES_PASSWORD in ("", "serviceability", "postgres", "password"):
            problems.append("POSTGRES_PASSWORD must not be a default value")
        if problems:
            raise ValueError(
                "Refusing to start in production:\n  - " + "\n  - ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

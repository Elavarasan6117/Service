"""Domain enumerations.

These are the vocabulary of the system. They are defined once and used by the
models, the schemas and the frontend (mirrored in ``frontend/src/types``).
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    SUPERVISOR = "SUPERVISOR"
    OPERATIONS = "OPERATIONS"
    SALES = "SALES"
    VIEW_ONLY = "VIEW_ONLY"


class ServiceStatus(StrEnum):
    """Status of a *customer* with respect to serviceability."""

    PENDING = "PENDING"
    CALCULATING = "CALCULATING"
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    LOCATION_VERIFICATION_REQUIRED = "LOCATION_VERIFICATION_REQUIRED"
    ROUTE_CALCULATION_ERROR = "ROUTE_CALCULATION_ERROR"
    NO_SERVICE_LOCATION_CONFIGURED = "NO_SERVICE_LOCATION_CONFIGURED"
    PENDING_REVIEW = "PENDING_REVIEW"

    @property
    def is_terminal(self) -> bool:
        return self in (ServiceStatus.AVAILABLE, ServiceStatus.NOT_AVAILABLE)

    @property
    def is_retryable(self) -> bool:
        return self in (
            ServiceStatus.ROUTE_CALCULATION_ERROR,
            ServiceStatus.LOCATION_VERIFICATION_REQUIRED,
            ServiceStatus.NO_SERVICE_LOCATION_CONFIGURED,
        )


class CheckResult(StrEnum):
    """Result recorded on an individual audit row.

    Deliberately narrower than ServiceStatus: an audit row records what the
    calculation concluded, not the workflow state of the customer. There is no
    CALCULATING or PENDING_REVIEW here because neither is a calculation result.
    """

    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    ROUTE_CALCULATION_ERROR = "ROUTE_CALCULATION_ERROR"
    NO_SERVICE_LOCATION_IN_RANGE = "NO_SERVICE_LOCATION_IN_RANGE"
    LOCATION_VERIFICATION_REQUIRED = "LOCATION_VERIFICATION_REQUIRED"


class DistanceType(StrEnum):
    """How a distance was measured.

    The serviceability decision accepts ROAD_DISTANCE only. STRAIGHT_LINE exists
    so that candidate-search diagnostics can be labelled honestly and so that a
    misuse is detectable at runtime rather than being an invisible bug.
    """

    ROAD_DISTANCE = "ROAD_DISTANCE"
    STRAIGHT_LINE = "STRAIGHT_LINE"


class LocationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    INVALID = "INVALID"


class EntityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class CoordinateSource(StrEnum):
    GEOCODED = "GEOCODED"
    MANUAL = "MANUAL"
    PROVIDED = "PROVIDED"
    IMPORTED = "IMPORTED"


class ImportBatchStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


class ImportRowStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"
    GEOCODE_FAILED = "GEOCODE_FAILED"
    COMMITTED = "COMMITTED"


class ConfigValueType(StrEnum):
    INT = "INT"
    FLOAT = "FLOAT"
    STRING = "STRING"
    BOOL = "BOOL"
    JSON = "JSON"


class Permission(StrEnum):
    VIEW = "VIEW"
    CUSTOMER_CREATE = "CUSTOMER_CREATE"
    CHECK_RUN = "CHECK_RUN"
    CUSTOMER_LOCATION_ADJUST = "CUSTOMER_LOCATION_ADJUST"
    SERVICE_LOCATION_WRITE = "SERVICE_LOCATION_WRITE"
    IMPORT_RUN = "IMPORT_RUN"
    DECISION_OVERRIDE = "DECISION_OVERRIDE"
    CONFIG_READ = "CONFIG_READ"
    CONFIG_WRITE_THRESHOLD = "CONFIG_WRITE_THRESHOLD"
    CONFIG_WRITE_GENERAL = "CONFIG_WRITE_GENERAL"
    USER_MANAGE = "USER_MANAGE"
    REPORT_EXPORT = "REPORT_EXPORT"
    AUDIT_DELETE = "AUDIT_DELETE"


ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.ADMIN: frozenset(Permission),
    UserRole.SUPERVISOR: frozenset(
        {
            Permission.VIEW,
            Permission.CUSTOMER_CREATE,
            Permission.CHECK_RUN,
            Permission.CUSTOMER_LOCATION_ADJUST,
            Permission.SERVICE_LOCATION_WRITE,
            Permission.IMPORT_RUN,
            Permission.DECISION_OVERRIDE,
            Permission.CONFIG_READ,
            Permission.CONFIG_WRITE_GENERAL,
            Permission.REPORT_EXPORT,
        }
    ),
    UserRole.OPERATIONS: frozenset(
        {
            Permission.VIEW,
            Permission.CUSTOMER_CREATE,
            Permission.CHECK_RUN,
            Permission.CUSTOMER_LOCATION_ADJUST,
            Permission.SERVICE_LOCATION_WRITE,
            Permission.CONFIG_READ,
            Permission.REPORT_EXPORT,
        }
    ),
    UserRole.SALES: frozenset(
        {
            Permission.VIEW,
            Permission.CUSTOMER_CREATE,
            Permission.CHECK_RUN,
            Permission.REPORT_EXPORT,
        }
    ),
    UserRole.VIEW_ONLY: frozenset({Permission.VIEW}),
}


def role_has(role: UserRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())

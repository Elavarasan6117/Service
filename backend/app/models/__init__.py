"""SQLAlchemy models.

Importing this package registers every mapper, which Alembic autogenerate and
``Base.metadata.create_all`` both rely on.
"""

from app.models.config import AppConfig, ConfigAudit
from app.models.customer import Customer
from app.models.imports import ImportBatch, ImportRow
from app.models.route import Route
from app.models.service_location import ServiceLocation
from app.models.serviceability_check import ServiceabilityCheck
from app.models.user import User
from app.models.warehouse import Warehouse

__all__ = [
    "AppConfig",
    "ConfigAudit",
    "Customer",
    "ImportBatch",
    "ImportRow",
    "Route",
    "ServiceLocation",
    "ServiceabilityCheck",
    "User",
    "Warehouse",
]

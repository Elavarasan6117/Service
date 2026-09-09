"""Customer lifecycle: create, geocode, locate, re-check.

The rule this module enforces (brief §13): a geocoding failure must NOT mark a
customer NOT_AVAILABLE. The customer is created, held at
LOCATION_VERIFICATION_REQUIRED, and the operator confirms the point on the map.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CoordinateSource, ServiceStatus
from app.core.errors import DuplicateResourceError, GeocodingError, NotFoundError
from app.core.logging import get_logger
from app.core.metrics import geocoding_results_total
from app.models.customer import Customer
from app.models.user import User
from app.providers.base import Coordinate
from app.services import events
from app.services.cache import RouteCache
from app.services.config_service import KEY_AUTO_CHECK_ON_CREATE, ConfigService
from app.services.serviceability import ServiceabilityEngine, ServiceabilityOutcome

logger = get_logger(__name__)


class CustomerService:
    def __init__(
        self,
        session: AsyncSession,
        geocoding_provider,
        engine: ServiceabilityEngine,
        config: ConfigService | None = None,
    ) -> None:
        self.session = session
        self.geocoding = geocoding_provider
        self.engine = engine
        self.config = config or ConfigService(session)

    # ------------------------------------------------------------------

    async def get(self, customer_id: uuid.UUID) -> Customer:
        customer = await self.session.get(Customer, customer_id)
        if customer is None:
            raise NotFoundError(f"No customer with id {customer_id}.")
        return customer

    async def get_by_code(self, code: str) -> Customer | None:
        result = await self.session.execute(
            select(Customer).where(Customer.customer_code == code)
        )
        return result.scalar_one_or_none()

    async def _assert_code_available(self, code: str) -> None:
        existing = await self.session.execute(
            select(func.count())
            .select_from(Customer)
            .where(Customer.customer_code == code)
        )
        if existing.scalar_one():
            raise DuplicateResourceError(
                f"A customer with code '{code}' already exists."
            )

    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        customer_code: str | None,
        customer_name: str,
        address: str,
        phone: str | None = None,
        area: str | None = None,
        city: str = "",
        pincode: str | None = None,
        service_type: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        actor: User | None = None,
    ) -> tuple[Customer, ServiceabilityOutcome | None]:
        customer_code = customer_code or await self._generate_customer_code()
        await self._assert_code_available(customer_code)

        customer = Customer(
            customer_code=customer_code,
            customer_name=customer_name,
            phone=phone,
            address=address,
            area=area,
            city=city,
            pincode=pincode,
            service_type=service_type,
            service_status=ServiceStatus.PENDING,
        )

        geocode_failed = False

        if latitude is not None and longitude is not None:
            # Coordinates supplied explicitly always win over geocoding: the
            # caller has better information than an address parser.
            customer.latitude = Decimal(str(latitude))
            customer.longitude = Decimal(str(longitude))
            customer.coordinate_source = CoordinateSource.PROVIDED
        else:
            try:
                result = await self.geocoding.geocode(self._geocode_query(customer))
                customer.latitude = Decimal(str(result.latitude))
                customer.longitude = Decimal(str(result.longitude))
                customer.formatted_address = result.formatted_address
                customer.coordinate_source = CoordinateSource.GEOCODED

                if result.needs_verification:
                    # We have a point, but not one we trust enough to decide on.
                    geocode_failed = True
                    customer.service_status = (
                        ServiceStatus.LOCATION_VERIFICATION_REQUIRED
                    )
                    geocoding_results_total.labels(outcome="partial").inc()
                    logger.info(
                        "geocode_low_confidence",
                        customer_code=customer_code,
                        confidence=result.confidence,
                        partial_match=result.partial_match,
                    )
                else:
                    geocoding_results_total.labels(outcome="success").inc()

            except GeocodingError as exc:
                # Explicitly NOT NOT_AVAILABLE. We failed to locate the
                # customer; we did not determine that they are too far away.
                geocode_failed = True
                customer.service_status = ServiceStatus.LOCATION_VERIFICATION_REQUIRED
                geocoding_results_total.labels(outcome="no_result").inc()
                logger.info(
                    "geocode_failed",
                    customer_code=customer_code,
                    error_code=exc.code,
                    address=address,
                )

        self.session.add(customer)
        await self.session.flush()

        await events.publish(
            events.EventType.CUSTOMER_CREATED,
            {
                "id": str(customer.id),
                "customerCode": customer.customer_code,
                "customerName": customer.customer_name,
                "latitude": float(customer.latitude) if customer.latitude else None,
                "longitude": float(customer.longitude) if customer.longitude else None,
                "status": customer.service_status.value,
            },
        )

        if geocode_failed or not await self.config.get_bool(KEY_AUTO_CHECK_ON_CREATE):
            return customer, None

        outcome = await self.run_check(customer, actor=actor)
        return customer, outcome

    async def _generate_customer_code(self) -> str:
        """Create a stable human-readable code when the operator omits one."""
        while True:
            code = f"CUST-{uuid.uuid4().hex[:10].upper()}"
            if not await self.get_by_code(code):
                return code

    def _geocode_query(self, customer: Customer) -> str:
        """Build the geocoding query from the most specific parts available.

        Including area and pincode materially improves Chennai geocoding
        accuracy -- a bare street name is frequently ambiguous across the city.
        """
        parts = [
            customer.address,
            customer.area,
            customer.city,
            customer.pincode,
            "Tamil Nadu, India",
        ]
        return ", ".join(p.strip() for p in parts if p and p.strip())

    # ------------------------------------------------------------------

    async def run_check(
        self,
        customer: Customer,
        *,
        actor: User | None = None,
        force_refresh: bool = False,
    ) -> ServiceabilityOutcome:
        await events.publish(
            events.EventType.CHECK_STARTED,
            {"customerId": str(customer.id), "customerCode": customer.customer_code},
        )

        outcome = await self.engine.check_customer(
            customer, actor=actor, force_refresh=force_refresh
        )

        payload = {
            "customerId": str(customer.id),
            "customerCode": customer.customer_code,
            "customerName": customer.customer_name,
            "latitude": float(customer.latitude) if customer.latitude else None,
            "longitude": float(customer.longitude) if customer.longitude else None,
            "status": outcome.status.value,
            "thresholdMeters": outcome.threshold_meters,
            "distanceMeters": (
                outcome.nearest.distance_meters if outcome.nearest else None
            ),
            "nearestServiceCode": (
                outcome.nearest.candidate.service_code if outcome.nearest else None
            ),
            "nearestServiceName": (
                outcome.nearest.candidate.location_name if outcome.nearest else None
            ),
            "checkId": str(outcome.check_id) if outcome.check_id else None,
        }
        event_type = (
            events.EventType.CHECK_ERROR
            if outcome.status
            in (
                ServiceStatus.ROUTE_CALCULATION_ERROR,
                ServiceStatus.LOCATION_VERIFICATION_REQUIRED,
            )
            else events.EventType.CHECK_COMPLETED
        )
        await events.publish(event_type, payload)
        return outcome

    # ------------------------------------------------------------------

    async def update_location(
        self,
        customer: Customer,
        latitude: float,
        longitude: float,
        *,
        actor: User | None = None,
        reason: str | None = None,
    ) -> ServiceabilityOutcome:
        """Manual map adjustment (brief §14).

        The customer's cached routes are invalidated before re-checking so the
        new position cannot be evaluated against distances measured from the
        old one.
        """
        old_point = None
        if customer.latitude is not None and customer.longitude is not None:
            old_point = Coordinate(float(customer.latitude), float(customer.longitude))

        customer.latitude = Decimal(str(latitude))
        customer.longitude = Decimal(str(longitude))
        customer.coordinate_source = CoordinateSource.MANUAL
        customer.service_status = ServiceStatus.CALCULATING
        await self.session.flush()

        cache = RouteCache(self.engine.routing.name)
        if old_point is not None:
            await cache.invalidate_point(old_point)
        await cache.invalidate_point(Coordinate(latitude, longitude))

        logger.info(
            "customer_location_adjusted",
            customer_code=customer.customer_code,
            latitude=latitude,
            longitude=longitude,
            actor=actor.username if actor else "SYSTEM",
            reason=reason,
        )

        await events.publish(
            events.EventType.CUSTOMER_LOCATION_UPDATED,
            {
                "customerId": str(customer.id),
                "latitude": latitude,
                "longitude": longitude,
            },
        )
        return await self.run_check(customer, actor=actor, force_refresh=True)

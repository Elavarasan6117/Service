"""The serviceability decision engine.

This module is the single source of truth for whether a customer is
serviceable. The frontend renders what this returns; it does not decide.

The one rule this file exists to enforce:

    ACTUAL ROAD DISTANCE <= threshold  ->  AVAILABLE
    ACTUAL ROAD DISTANCE >  threshold  ->  NOT_AVAILABLE

with three corollaries that are just as important:

  * A straight-line distance can never produce that decision. ``_decide``
    rejects any measurement not tagged ROAD_DISTANCE, and the database has a
    matching CHECK constraint.
  * A provider failure can never produce NOT_AVAILABLE. Failing to measure is
    not a measurement.
  * Every decision is written to an append-only audit row together with the
    threshold that produced it.

See docs/04-serviceability-algorithm.md for the normative specification.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CheckResult, DistanceType, ServiceStatus
from app.core.errors import (
    GeocodingError,
    InvalidDistanceTypeError,
    ProviderError,
)
from app.core.logging import get_logger
from app.core.metrics import (
    serviceability_candidates,
    serviceability_checks_total,
    serviceability_distance_meters,
    serviceability_duration_seconds,
)
from app.models.customer import Customer
from app.models.serviceability_check import ServiceabilityCheck
from app.models.user import User
from app.providers.base import Coordinate, RouteLeg
from app.services.cache import RouteCache
from app.services.config_service import (
    KEY_CANDIDATE_FACTOR,
    KEY_CANDIDATE_MAX,
    KEY_CACHE_TTL,
    KEY_DECISION_ENABLED,
    KEY_EMPTY_TABLE_RESULT,
    ConfigService,
)
from app.services.spatial_search import Candidate, SpatialSearchService

logger = get_logger(__name__)


@dataclass(slots=True)
class NearestMatch:
    candidate: Candidate
    distance_meters: int
    duration_seconds: int | None
    provider: str


@dataclass(slots=True)
class ServiceabilityOutcome:
    """What the engine concluded, before it is turned into an HTTP response."""

    status: ServiceStatus
    result: CheckResult
    threshold_meters: int
    check_id: uuid.UUID | None = None
    nearest: NearestMatch | None = None
    route_geometry: str | None = None
    route_geometry_format: str | None = None
    candidate_count: int = 0
    candidate_radius_meters: int | None = None
    cache_hit: bool = False
    degraded: bool = False
    error_code: str | None = None
    error_detail: str | None = None
    reason: str | None = None
    message: str | None = None
    duration_ms: int = 0
    calculated_at: datetime | None = None
    diagnostics: dict = field(default_factory=dict)

    @property
    def retryable(self) -> bool:
        return self.status.is_retryable

    @property
    def margin_meters(self) -> int | None:
        if self.nearest is None:
            return None
        return self.threshold_meters - self.nearest.distance_meters


class ServiceabilityEngine:
    def __init__(
        self,
        session: AsyncSession,
        routing_provider,
        config: ConfigService | None = None,
        spatial: SpatialSearchService | None = None,
    ) -> None:
        self.session = session
        self.routing = routing_provider
        self.config = config or ConfigService(session)
        self.spatial = spatial or SpatialSearchService(session)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def check_customer(
        self,
        customer: Customer,
        *,
        actor: User | None = None,
        force_refresh: bool = False,
    ) -> ServiceabilityOutcome:
        """Run a check for a persisted customer and update its summary fields."""
        outcome = await self.evaluate(
            latitude=float(customer.latitude) if customer.latitude is not None else None,
            longitude=float(customer.longitude)
            if customer.longitude is not None
            else None,
            customer_id=customer.id,
            actor=actor,
            force_refresh=force_refresh,
            service_type=customer.service_type,
            area=customer.area,
        )

        customer.service_status = outcome.status
        customer.last_check_id = outcome.check_id
        if outcome.nearest is not None:
            customer.nearest_service_location_id = outcome.nearest.candidate.id
            customer.nearest_service_distance_meters = outcome.nearest.distance_meters
        else:
            # A failed or out-of-range check must not leave a stale "nearest"
            # from a previous run sitting on the record.
            customer.nearest_service_location_id = None
            customer.nearest_service_distance_meters = None

        await self.session.flush()
        return outcome

    async def evaluate(
        self,
        *,
        latitude: float | None,
        longitude: float | None,
        customer_id: uuid.UUID | None = None,
        actor: User | None = None,
        force_refresh: bool = False,
        service_type: str | None = None,
        area: str | None = None,
    ) -> ServiceabilityOutcome:
        """Evaluate serviceability for a coordinate. Always writes an audit row."""
        started = time.perf_counter()
        request_timestamp = datetime.now(timezone.utc)

        threshold = await self.config.get_threshold_meters(
            service_type=service_type, area=area
        )

        # Step 3 — no coordinates means we cannot measure anything.
        if latitude is None or longitude is None:
            return await self._finalise(
                ServiceabilityOutcome(
                    status=ServiceStatus.LOCATION_VERIFICATION_REQUIRED,
                    result=CheckResult.LOCATION_VERIFICATION_REQUIRED,
                    threshold_meters=threshold,
                    error_code="COORDINATES_MISSING",
                    message=(
                        "This customer has no confirmed coordinates. "
                        "Place the marker on the map and confirm the location."
                    ),
                ),
                customer_id=customer_id,
                latitude=latitude,
                longitude=longitude,
                actor=actor,
                request_timestamp=request_timestamp,
                started=started,
            )

        origin = Coordinate(latitude, longitude)

        # Automatic decisions can be switched off during an incident
        # (rollback runbook step 1) without taking the application down.
        if not await self.config.get_bool(KEY_DECISION_ENABLED):
            return await self._finalise(
                ServiceabilityOutcome(
                    status=ServiceStatus.PENDING_REVIEW,
                    result=CheckResult.LOCATION_VERIFICATION_REQUIRED,
                    threshold_meters=threshold,
                    error_code="AUTOMATIC_DECISION_DISABLED",
                    message=(
                        "Automatic serviceability decisions are currently disabled. "
                        "This customer has been queued for manual review."
                    ),
                ),
                customer_id=customer_id,
                latitude=latitude,
                longitude=longitude,
                actor=actor,
                request_timestamp=request_timestamp,
                started=started,
            )

        # Step 4 — nothing configured to compare against.
        if await self.spatial.count_active_locations() == 0:
            empty_status = await self._empty_table_status()
            return await self._finalise(
                ServiceabilityOutcome(
                    status=empty_status,
                    result=CheckResult.NO_SERVICE_LOCATION_IN_RANGE,
                    threshold_meters=threshold,
                    candidate_count=0,
                    message=(
                        "No active service locations are configured. "
                        "Import or activate service locations before running checks."
                    ),
                ),
                customer_id=customer_id,
                latitude=latitude,
                longitude=longitude,
                actor=actor,
                request_timestamp=request_timestamp,
                started=started,
            )

        # Step 5 — Stage 1 spatial candidate search. No external calls, no cost.
        factor = await self.config.get_float(KEY_CANDIDATE_FACTOR)
        max_candidates = await self.config.get_int(KEY_CANDIDATE_MAX)
        candidate_radius = int(round(threshold * factor))

        candidates = await self.spatial.find_candidates(
            latitude, longitude, candidate_radius, max_candidates
        )
        serviceability_candidates.observe(len(candidates))

        # Step 6 — nothing within the (generously widened) search radius.
        # Road distance is always >= straight-line distance, so nothing here
        # can be within the threshold by road either.
        if not candidates:
            # The customer-facing status is NOT_AVAILABLE -- that is the honest
            # business answer. The audit *result* is NO_SERVICE_LOCATION_IN_RANGE
            # because no distance was ever measured, and the schema (rightly)
            # refuses to record an AVAILABLE/NOT_AVAILABLE result without a road
            # distance to justify it.
            return await self._finalise(
                ServiceabilityOutcome(
                    status=ServiceStatus.NOT_AVAILABLE,
                    result=CheckResult.NO_SERVICE_LOCATION_IN_RANGE,
                    threshold_meters=threshold,
                    candidate_count=0,
                    candidate_radius_meters=candidate_radius,
                    reason=(
                        f"No active service location lies within {candidate_radius} m "
                        f"in a straight line, so none can be within the "
                        f"{threshold} m road-distance limit."
                    ),
                ),
                customer_id=customer_id,
                latitude=latitude,
                longitude=longitude,
                actor=actor,
                request_timestamp=request_timestamp,
                started=started,
            )

        # Step 7 — Stage 2: actual road distances.
        try:
            legs, cache_hit = await self._road_distances(
                origin, candidates, force_refresh=force_refresh
            )
        except ProviderError as exc:
            logger.warning(
                "routing_failed",
                customer_id=str(customer_id) if customer_id else None,
                error_code=exc.code,
                error=exc.message,
                candidates=len(candidates),
            )
            # THE critical branch: a routing failure is never NOT_AVAILABLE.
            return await self._finalise(
                ServiceabilityOutcome(
                    status=ServiceStatus.ROUTE_CALCULATION_ERROR,
                    result=CheckResult.ROUTE_CALCULATION_ERROR,
                    threshold_meters=threshold,
                    candidate_count=len(candidates),
                    candidate_radius_meters=candidate_radius,
                    error_code=exc.code,
                    error_detail=exc.message,
                    message="Unable to calculate driving distance. Please retry.",
                ),
                customer_id=customer_id,
                latitude=latitude,
                longitude=longitude,
                actor=actor,
                request_timestamp=request_timestamp,
                started=started,
            )

        resolved = [(c, leg) for c, leg in zip(candidates, legs) if leg is not None]

        # Step 8 — every destination came back unreachable.
        if not resolved:
            return await self._finalise(
                ServiceabilityOutcome(
                    status=ServiceStatus.ROUTE_CALCULATION_ERROR,
                    result=CheckResult.ROUTE_CALCULATION_ERROR,
                    threshold_meters=threshold,
                    candidate_count=len(candidates),
                    candidate_radius_meters=candidate_radius,
                    error_code="NO_ROUTE_TO_ANY_CANDIDATE",
                    error_detail=(
                        "The routing provider could not find a driving route to any "
                        "candidate service location."
                    ),
                    message="Unable to calculate driving distance. Please retry.",
                ),
                customer_id=customer_id,
                latitude=latitude,
                longitude=longitude,
                actor=actor,
                request_timestamp=request_timestamp,
                started=started,
            )

        degraded = len(resolved) < len(candidates)

        # Step 9 — the nearest by ROAD distance, which is frequently not the
        # nearest in a straight line.
        best_candidate, best_leg = min(resolved, key=lambda pair: pair[1].distance_meters)
        nearest = NearestMatch(
            candidate=best_candidate,
            distance_meters=best_leg.distance_meters,
            duration_seconds=best_leg.duration_seconds,
            provider=best_leg.provider,
        )

        # Step 10 — the decision.
        result = self._decide(best_leg, threshold)
        status = (
            ServiceStatus.AVAILABLE
            if result is CheckResult.AVAILABLE
            else ServiceStatus.NOT_AVAILABLE
        )
        serviceability_distance_meters.observe(nearest.distance_meters)

        # Step 11 — route geometry for the winning pair only. Failure here is
        # cosmetic: the decision already stands.
        geometry: str | None = None
        geometry_format: str | None = None
        try:
            detail = await self.routing.get_driving_route(
                origin,
                Coordinate(best_candidate.latitude, best_candidate.longitude),
            )
            geometry = detail.geometry
            geometry_format = detail.geometry_format
        except ProviderError as exc:
            logger.info("route_geometry_unavailable", error=exc.code)

        reason = None
        if result is CheckResult.NOT_AVAILABLE:
            reason = (
                "Customer is outside the existing service coverage based on "
                "road distance."
            )

        return await self._finalise(
            ServiceabilityOutcome(
                status=status,
                result=result,
                threshold_meters=threshold,
                nearest=nearest,
                route_geometry=geometry,
                route_geometry_format=geometry_format,
                candidate_count=len(candidates),
                candidate_radius_meters=candidate_radius,
                cache_hit=cache_hit,
                degraded=degraded,
                reason=reason,
                diagnostics={
                    "straight_line_meters_to_nearest": best_candidate.straight_line_meters,
                    "resolved_candidates": len(resolved),
                },
            ),
            customer_id=customer_id,
            latitude=latitude,
            longitude=longitude,
            actor=actor,
            request_timestamp=request_timestamp,
            started=started,
        )

    # ------------------------------------------------------------------
    # The decision itself
    # ------------------------------------------------------------------

    @staticmethod
    def _decide(leg: RouteLeg, threshold_meters: int) -> CheckResult:
        """Compare a road distance against the threshold.

        Deliberately tiny and deliberately guarded. Two properties matter:

        1. ``leg.distance_type`` must be ROAD_DISTANCE. A straight-line value
           reaching this point is a programming error, and the system fails
           loudly rather than returning a plausible-looking wrong answer.
        2. Both operands are ``int`` metres, so ``<=`` is exact. 2000 m is
           AVAILABLE; 2001 m is not. There is no float epsilon anywhere.
        """
        if leg.distance_type is not DistanceType.ROAD_DISTANCE:
            raise InvalidDistanceTypeError(
                f"Refusing to decide serviceability from a "
                f"{leg.distance_type} measurement."
            )
        if not isinstance(leg.distance_meters, int) or not isinstance(
            threshold_meters, int
        ):
            raise InvalidDistanceTypeError(
                "Serviceability comparison requires integer metres on both sides."
            )
        return (
            CheckResult.AVAILABLE
            if leg.distance_meters <= threshold_meters
            else CheckResult.NOT_AVAILABLE
        )

    # ------------------------------------------------------------------
    # Stage 2 helpers
    # ------------------------------------------------------------------

    async def _road_distances(
        self,
        origin: Coordinate,
        candidates: list[Candidate],
        *,
        force_refresh: bool,
    ) -> tuple[list[RouteLeg | None], bool]:
        """Road distance for each candidate, using the cache where possible."""
        destinations = [Coordinate(c.latitude, c.longitude) for c in candidates]
        ttl = await self.config.get_int(KEY_CACHE_TTL)
        cache = RouteCache(self.routing.name, ttl_seconds=ttl)

        cached: dict[int, RouteLeg] = {}
        if not force_refresh:
            cached = await cache.get_many(origin, destinations)

        missing_indices = [i for i in range(len(destinations)) if i not in cached]

        fetched: dict[int, RouteLeg | None] = {}
        if missing_indices:
            missing_destinations = [destinations[i] for i in missing_indices]
            legs = await self.routing.get_driving_distance(
                origin, missing_destinations
            )
            to_store: list[tuple[Coordinate, RouteLeg]] = []
            for position, index in enumerate(missing_indices):
                leg = legs[position] if position < len(legs) else None
                fetched[index] = leg
                if leg is not None:
                    to_store.append((destinations[index], leg))
            # Successful measurements only -- a transient failure must not
            # become a sticky cached "unreachable".
            await cache.set_many(origin, to_store)

        ordered: list[RouteLeg | None] = []
        for index in range(len(destinations)):
            ordered.append(cached.get(index) or fetched.get(index))

        # Only report a cache hit if the whole check avoided routing entirely;
        # a partial hit still costs a provider call.
        return ordered, bool(cached) and not missing_indices

    async def _empty_table_status(self) -> ServiceStatus:
        configured = await self.config.get(KEY_EMPTY_TABLE_RESULT)
        try:
            return ServiceStatus(str(configured))
        except ValueError:
            logger.warning(
                "invalid_empty_table_result_config",
                configured=configured,
                fallback=ServiceStatus.NO_SERVICE_LOCATION_CONFIGURED.value,
            )
            return ServiceStatus.NO_SERVICE_LOCATION_CONFIGURED

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    async def _finalise(
        self,
        outcome: ServiceabilityOutcome,
        *,
        customer_id: uuid.UUID | None,
        latitude: float | None,
        longitude: float | None,
        actor: User | None,
        request_timestamp: datetime,
        started: float,
    ) -> ServiceabilityOutcome:
        """Persist the audit row. Every path through the engine ends here."""
        response_timestamp = datetime.now(timezone.utc)
        duration_ms = int((time.perf_counter() - started) * 1000)

        check = ServiceabilityCheck(
            customer_id=customer_id,
            nearest_service_location_id=(
                outcome.nearest.candidate.id if outcome.nearest else None
            ),
            customer_latitude=latitude,
            customer_longitude=longitude,
            calculated_distance_meters=(
                outcome.nearest.distance_meters if outcome.nearest else None
            ),
            # Always ROAD_DISTANCE. There is no branch that writes anything else.
            distance_type=DistanceType.ROAD_DISTANCE,
            routing_provider=(
                outcome.nearest.provider if outcome.nearest else self.routing.name
            ),
            route_duration_seconds=(
                outcome.nearest.duration_seconds if outcome.nearest else None
            ),
            route_geometry=outcome.route_geometry,
            route_geometry_format=outcome.route_geometry_format,
            threshold_meters=outcome.threshold_meters,
            result=outcome.result,
            candidate_count=outcome.candidate_count,
            candidate_radius_meters=outcome.candidate_radius_meters,
            cache_hit=outcome.cache_hit,
            degraded=outcome.degraded,
            error_code=outcome.error_code,
            error_detail=outcome.error_detail,
            reason=outcome.reason,
            request_timestamp=request_timestamp,
            response_timestamp=response_timestamp,
            duration_ms=duration_ms,
            created_by=actor.id if actor else None,
            created_by_label=actor.label if actor else "SYSTEM",
        )
        self.session.add(check)
        await self.session.flush()

        outcome.check_id = check.id
        outcome.duration_ms = duration_ms
        outcome.calculated_at = response_timestamp

        serviceability_checks_total.labels(result=outcome.result.value).inc()
        serviceability_duration_seconds.observe(duration_ms / 1000.0)

        logger.info(
            "serviceability_check",
            check_id=str(check.id),
            customer_id=str(customer_id) if customer_id else None,
            result=outcome.result.value,
            status=outcome.status.value,
            distance_meters=(
                outcome.nearest.distance_meters if outcome.nearest else None
            ),
            threshold_meters=outcome.threshold_meters,
            distance_type=DistanceType.ROAD_DISTANCE.value,
            nearest_service_code=(
                outcome.nearest.candidate.service_code if outcome.nearest else None
            ),
            candidate_count=outcome.candidate_count,
            cache_hit=outcome.cache_hit,
            degraded=outcome.degraded,
            provider=self.routing.name,
            duration_ms=duration_ms,
            actor=actor.username if actor else "SYSTEM",
        )
        return outcome


__all__ = [
    "ServiceabilityEngine",
    "ServiceabilityOutcome",
    "NearestMatch",
    "GeocodingError",
]

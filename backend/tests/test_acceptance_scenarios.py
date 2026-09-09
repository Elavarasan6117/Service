"""The ten acceptance scenarios from brief §25.

Each test maps one-to-one onto a numbered scenario. The fake routing provider
returns exact metre values, so boundary behaviour is asserted precisely rather
than approximately.

A note on how these are constructed: the service locations are placed at
geographic offsets, and the road distances are supplied independently by the
fake provider keyed on those coordinates. That deliberate mismatch between
straight-line geometry and road distance is what makes the tests meaningful --
if the engine ever decided on straight-line distance, several of these would
fail.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.enums import CheckResult, ServiceStatus
from app.core.errors import InvalidDistanceTypeError
from app.models.serviceability_check import ServiceabilityCheck
from app.providers.base import RouteLeg
from app.providers.fake import FakeRoutingProvider
from app.core.enums import DistanceType
from sqlalchemy import select

from tests.conftest import ANNA_NAGAR, make_engine

# A degree of latitude is ~111 km, so these offsets give predictable
# straight-line ordering without being anywhere near the threshold.
def offset(base: tuple[float, float], north_m: float, east_m: float) -> tuple[float, float]:
    lat = base[0] + north_m / 111_320.0
    lng = base[1] + east_m / (111_320.0 * 0.9743)  # cos(13.08°)
    # Six decimal places to match the cache-key and fake-provider rounding.
    return round(lat, 6), round(lng, 6)


async def _run(session, customer, road_distances, **provider_kwargs):
    routing = FakeRoutingProvider(distances=road_distances, **provider_kwargs)
    engine = make_engine(session, routing)
    outcome = await engine.check_customer(customer)
    await session.commit()
    return outcome, routing


# ---------------------------------------------------------------------------
# TEST 1 — 500 m road distance -> AVAILABLE
# ---------------------------------------------------------------------------


async def test_1_five_hundred_metres_is_available(session, customer, locations):
    point = offset(ANNA_NAGAR, 400, 0)
    await locations.create(*point, code="SRV-101")

    outcome, _ = await _run(session, customer, {point: 500})

    assert outcome.status is ServiceStatus.AVAILABLE
    assert outcome.result is CheckResult.AVAILABLE
    assert outcome.nearest.distance_meters == 500
    assert outcome.nearest.candidate.service_code == "SRV-101"
    assert outcome.margin_meters == 1500


# ---------------------------------------------------------------------------
# TEST 2 — 1.5 km road distance -> AVAILABLE
# ---------------------------------------------------------------------------


async def test_2_one_point_five_km_is_available(session, customer, locations):
    point = offset(ANNA_NAGAR, 1200, 0)
    await locations.create(*point, code="SRV-102")

    outcome, _ = await _run(session, customer, {point: 1500})

    assert outcome.status is ServiceStatus.AVAILABLE
    assert outcome.nearest.distance_meters == 1500
    assert outcome.margin_meters == 500


# ---------------------------------------------------------------------------
# TEST 3 — EXACTLY 2000 m -> AVAILABLE  (brief §17)
# ---------------------------------------------------------------------------


async def test_3_exactly_two_thousand_metres_is_available(session, customer, locations):
    point = offset(ANNA_NAGAR, 1500, 0)
    await locations.create(*point, code="SRV-103")

    outcome, _ = await _run(session, customer, {point: 2000})

    assert outcome.nearest.distance_meters == 2000
    assert outcome.status is ServiceStatus.AVAILABLE, (
        "A distance exactly equal to the threshold must be AVAILABLE. "
        "This is the boundary the brief calls out explicitly."
    )
    assert outcome.margin_meters == 0


async def test_3b_two_thousand_and_one_metres_is_not_available(
    session, customer, locations
):
    """The other side of the boundary. 2000.01 m rounds to 2001 -> NOT_AVAILABLE."""
    point = offset(ANNA_NAGAR, 1500, 0)
    await locations.create(*point, code="SRV-103B")

    outcome, _ = await _run(session, customer, {point: 2001})

    assert outcome.status is ServiceStatus.NOT_AVAILABLE
    assert outcome.margin_meters == -1


# ---------------------------------------------------------------------------
# TEST 4 — 2.1 km -> NOT_AVAILABLE
# ---------------------------------------------------------------------------


async def test_4_two_point_one_km_is_not_available(session, customer, locations):
    point = offset(ANNA_NAGAR, 1600, 0)
    await locations.create(*point, code="SRV-104")

    outcome, _ = await _run(session, customer, {point: 2100})

    assert outcome.status is ServiceStatus.NOT_AVAILABLE
    assert outcome.result is CheckResult.NOT_AVAILABLE
    assert outcome.nearest.distance_meters == 2100
    assert outcome.reason and "road distance" in outcome.reason.lower()
    # Still reports which location is nearest, so operations can see how far out.
    assert outcome.nearest.candidate.service_code == "SRV-104"


# ---------------------------------------------------------------------------
# TEST 5 — nearest by ROAD is not the nearest in a straight line
# ---------------------------------------------------------------------------


async def test_5_nearest_is_chosen_by_road_distance_not_straight_line(
    session, customer, locations
):
    """The decisive test.

    ``near_geo`` is physically closest but 4 km away by road (a river, a
    one-way system, a railway line -- any real-world barrier). ``far_geo`` is
    further as the crow flies but only 1.8 km by road.

    A system using straight-line distance would pick ``near_geo``, see 4 km and
    answer NOT_AVAILABLE. The correct answer is AVAILABLE via ``far_geo``.
    """
    near_geo = offset(ANNA_NAGAR, 300, 0)     # ~300 m straight line
    far_geo = offset(ANNA_NAGAR, 1400, 0)     # ~1400 m straight line

    await locations.create(*near_geo, code="SRV-NEAR-GEO")
    await locations.create(*far_geo, code="SRV-NEAR-ROAD")

    outcome, _ = await _run(
        session,
        customer,
        {near_geo: 4000, far_geo: 1800},
    )

    assert outcome.status is ServiceStatus.AVAILABLE
    assert outcome.nearest.distance_meters == 1800
    assert outcome.nearest.candidate.service_code == "SRV-NEAR-ROAD", (
        "The nearest service location must be selected by ACTUAL ROAD DISTANCE. "
        "Selecting SRV-NEAR-GEO would mean the straight-line distance drove the "
        "decision."
    )
    assert outcome.candidate_count == 2


# ---------------------------------------------------------------------------
# TEST 6 — no service locations configured
# ---------------------------------------------------------------------------


async def test_6_no_service_locations_configured(session, customer):
    outcome, routing = await _run(session, customer, {})

    assert outcome.status is ServiceStatus.NO_SERVICE_LOCATION_CONFIGURED
    assert outcome.result is CheckResult.NO_SERVICE_LOCATION_IN_RANGE
    assert outcome.candidate_count == 0
    assert routing.call_count == 0, (
        "With no service locations there is nothing to route to; the system "
        "must not spend a paid routing call to discover that."
    )


async def test_6b_empty_table_result_is_configurable(session, customer):
    """The business can choose NOT_AVAILABLE instead, per brief §25 test 6."""
    from app.models.config import AppConfig

    row = await session.get(AppConfig, "SERVICEABILITY_EMPTY_TABLE_RESULT")
    row.value = "NOT_AVAILABLE"
    await session.commit()

    outcome, _ = await _run(session, customer, {})
    assert outcome.status is ServiceStatus.NOT_AVAILABLE


async def test_6c_locations_exist_but_none_in_range(session, customer, locations):
    """Distinct from an unconfigured system: this is a genuine coverage answer."""
    far = offset(ANNA_NAGAR, 30_000, 0)  # 30 km away, well outside 8 km Stage-1
    await locations.create(*far, code="SRV-FAR")

    outcome, routing = await _run(session, customer, {far: 35_000})

    # Customer-facing answer is NOT_AVAILABLE (a real coverage answer), while
    # the audit row records NO_SERVICE_LOCATION_IN_RANGE because no distance
    # was measured. The schema constraint enforces that separation.
    assert outcome.status is ServiceStatus.NOT_AVAILABLE
    assert outcome.result is CheckResult.NO_SERVICE_LOCATION_IN_RANGE
    assert outcome.candidate_count == 0
    assert routing.call_count == 0, (
        "Stage 1 excluded every location, so Stage 2 must not run. This is the "
        "cost-control property of the two-stage design."
    )


# ---------------------------------------------------------------------------
# TEST 7 — invalid address -> LOCATION_VERIFICATION_REQUIRED
# ---------------------------------------------------------------------------


async def test_7_invalid_address_requires_verification(session, locations, ops_user):
    from app.providers.fake import FakeGeocodingProvider
    from app.services.customer_service import CustomerService

    point = offset(ANNA_NAGAR, 500, 0)
    await locations.create(*point, code="SRV-107")

    geocoding = FakeGeocodingProvider(fail_for={"Nowhere Street, Chennai, Tamil Nadu, India"})
    engine = make_engine(session, FakeRoutingProvider(distances={point: 900}))
    service = CustomerService(session, geocoding, engine)

    customer, outcome = await service.create(
        customer_code="CUST-BAD-ADDR",
        customer_name="Unfindable Traders",
        address="Nowhere Street",
        city="Chennai",
        actor=ops_user,
    )
    await session.commit()

    assert customer.service_status is ServiceStatus.LOCATION_VERIFICATION_REQUIRED
    assert customer.latitude is None
    assert outcome is None, "No check should run without coordinates."
    assert customer.service_status is not ServiceStatus.NOT_AVAILABLE, (
        "A geocoding failure must never be reported as NOT_AVAILABLE -- we "
        "failed to locate the customer, we did not measure them as too far."
    )


async def test_7b_low_confidence_geocode_requires_verification(
    session, locations, ops_user
):
    from app.providers.fake import FakeGeocodingProvider
    from app.services.customer_service import CustomerService

    point = offset(ANNA_NAGAR, 500, 0)
    await locations.create(*point, code="SRV-107B")

    geocoding = FakeGeocodingProvider(confidence="low")
    engine = make_engine(session, FakeRoutingProvider(distances={point: 900}))
    service = CustomerService(session, geocoding, engine)

    customer, outcome = await service.create(
        customer_code="CUST-VAGUE",
        customer_name="Approximate Stores",
        address="Somewhere near the main road",
        actor=ops_user,
    )
    await session.commit()

    assert customer.service_status is ServiceStatus.LOCATION_VERIFICATION_REQUIRED
    # We do keep the approximate point so the operator has somewhere to start.
    assert customer.latitude is not None
    assert outcome is None


# ---------------------------------------------------------------------------
# TEST 8 — routing failure -> ROUTE_CALCULATION_ERROR, never NOT_AVAILABLE
# ---------------------------------------------------------------------------


async def test_8_routing_failure_is_not_not_available(session, customer, locations):
    point = offset(ANNA_NAGAR, 500, 0)
    await locations.create(*point, code="SRV-108")

    outcome, _ = await _run(session, customer, {point: 900}, fail=True)

    assert outcome.status is ServiceStatus.ROUTE_CALCULATION_ERROR
    assert outcome.result is CheckResult.ROUTE_CALCULATION_ERROR
    assert outcome.status is not ServiceStatus.NOT_AVAILABLE, (
        "Failing to measure a distance is not a measurement. Reporting "
        "NOT_AVAILABLE here would reject a customer we never actually assessed."
    )
    assert outcome.retryable is True
    assert outcome.message == "Unable to calculate driving distance. Please retry."
    assert outcome.nearest is None
    assert outcome.candidate_count == 1  # Stage 1 did find a candidate


async def test_8b_routing_failure_is_still_audited(session, customer, locations):
    point = offset(ANNA_NAGAR, 500, 0)
    await locations.create(*point, code="SRV-108B")

    outcome, _ = await _run(session, customer, {point: 900}, fail=True)

    check = await session.get(ServiceabilityCheck, outcome.check_id)
    assert check is not None
    assert check.result is CheckResult.ROUTE_CALCULATION_ERROR
    assert check.threshold_meters == 2000, "The threshold is recorded even on failure."
    assert check.error_code is not None
    assert check.customer_latitude is not None


async def test_8c_partial_unreachability_still_decides(session, customer, locations):
    """One unreachable destination must not fail the whole check."""
    good = offset(ANNA_NAGAR, 1000, 0)
    bad = offset(ANNA_NAGAR, 1100, 0)
    await locations.create(*good, code="SRV-GOOD")
    await locations.create(*bad, code="SRV-UNREACHABLE")

    routing = FakeRoutingProvider(
        distances={good: 1400}, unreachable={(round(bad[0], 6), round(bad[1], 6))}
    )
    engine = make_engine(session, routing)
    outcome = await engine.check_customer(customer)
    await session.commit()

    assert outcome.status is ServiceStatus.AVAILABLE
    assert outcome.nearest.candidate.service_code == "SRV-GOOD"
    assert outcome.degraded is True


async def test_8d_all_unreachable_is_route_calculation_error(
    session, customer, locations
):
    point = offset(ANNA_NAGAR, 900, 0)
    await locations.create(*point, code="SRV-108D")

    routing = FakeRoutingProvider(
        unreachable={(round(point[0], 6), round(point[1], 6))}
    )
    engine = make_engine(session, routing)
    outcome = await engine.check_customer(customer)
    await session.commit()

    assert outcome.status is ServiceStatus.ROUTE_CALCULATION_ERROR
    assert outcome.error_code == "NO_ROUTE_TO_ANY_CANDIDATE"


# ---------------------------------------------------------------------------
# TEST 9 — marker moved manually -> recalculated
# ---------------------------------------------------------------------------


async def test_9_manual_marker_move_recalculates(session, customer, locations, ops_user):
    from app.providers.fake import FakeGeocodingProvider
    from app.services.customer_service import CustomerService

    # A location that is out of range from where the customer currently sits,
    # but in range from where the operator will move them to.
    target = offset(ANNA_NAGAR, 2500, 0)
    await locations.create(*target, code="SRV-109")

    moved_to = offset(ANNA_NAGAR, 2000, 0)

    routing = FakeRoutingProvider(distances={target: 3200})
    engine = make_engine(session, routing)
    service = CustomerService(session, FakeGeocodingProvider(), engine)

    first = await service.run_check(customer, actor=ops_user)
    await session.commit()
    assert first.status is ServiceStatus.NOT_AVAILABLE

    # After the move, the road distance to the same location is short.
    routing.distances = {target: 800}

    second = await service.update_location(
        customer, moved_to[0], moved_to[1], actor=ops_user, reason="Corrected on map"
    )
    await session.commit()

    assert second.status is ServiceStatus.AVAILABLE
    assert second.nearest.distance_meters == 800
    assert float(customer.latitude) == pytest.approx(moved_to[0])
    assert customer.coordinate_source.value == "MANUAL"

    # Both decisions survive in the audit trail.
    checks = (
        (
            await session.execute(
                select(ServiceabilityCheck)
                .where(ServiceabilityCheck.customer_id == customer.id)
                .order_by(ServiceabilityCheck.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(checks) == 2
    assert {c.result for c in checks} == {
        CheckResult.NOT_AVAILABLE,
        CheckResult.AVAILABLE,
    }


# ---------------------------------------------------------------------------
# TEST 10 — service location moved -> later checks use the new position
# ---------------------------------------------------------------------------


async def test_10_moved_service_location_is_used_in_new_checks(
    session, customer, locations
):
    original = offset(ANNA_NAGAR, 3000, 0)
    location = await locations.create(*original, code="SRV-110")

    routing = FakeRoutingProvider(distances={original: 3500})
    engine = make_engine(session, routing)
    first = await engine.check_customer(customer)
    await session.commit()
    assert first.status is ServiceStatus.NOT_AVAILABLE

    # The location moves much closer to the customer.
    relocated = offset(ANNA_NAGAR, 900, 0)
    location.latitude = Decimal(str(relocated[0]))
    location.longitude = Decimal(str(relocated[1]))
    await session.commit()

    routing.distances = {relocated: 1200}
    second = await engine.check_customer(customer)
    await session.commit()

    assert second.status is ServiceStatus.AVAILABLE
    assert second.nearest.distance_meters == 1200
    assert second.nearest.candidate.service_code == "SRV-110"


# ---------------------------------------------------------------------------
# Guard rails that the ten scenarios imply but do not state
# ---------------------------------------------------------------------------


def test_decision_rejects_straight_line_measurement():
    """The engine must refuse to decide from a non-road measurement.

    This is a programming-error guard. If a future provider adapter ever
    returned a geodesic approximation, the system fails loudly instead of
    quietly producing a wrong but plausible decision.
    """
    from app.services.serviceability import ServiceabilityEngine

    leg = RouteLeg(
        distance_meters=1000,
        distance_type=DistanceType.STRAIGHT_LINE,
        provider="bad",
    )
    with pytest.raises(InvalidDistanceTypeError):
        ServiceabilityEngine._decide(leg, 2000)


def test_decision_is_exact_at_every_boundary_metre():
    from app.services.serviceability import ServiceabilityEngine

    for metres, expected in [
        (0, CheckResult.AVAILABLE),
        (1, CheckResult.AVAILABLE),
        (1999, CheckResult.AVAILABLE),
        (2000, CheckResult.AVAILABLE),
        (2001, CheckResult.NOT_AVAILABLE),
        (5000, CheckResult.NOT_AVAILABLE),
    ]:
        leg = RouteLeg(distance_meters=metres, provider="test")
        assert ServiceabilityEngine._decide(leg, 2000) is expected, (
            f"{metres} m against a 2000 m threshold should be {expected.value}"
        )


def test_route_leg_rejects_non_integer_metres():
    """Rounding happens once, at the adapter boundary."""
    with pytest.raises(TypeError):
        RouteLeg(distance_meters=2000.4, provider="test")  # type: ignore[arg-type]

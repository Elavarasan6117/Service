"""API-level tests: auth, RBAC, customer flow, the canonical response shape."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.enums import EntityStatus
from app.models.customer import Customer
from app.models.service_location import ServiceLocation
from app.models.warehouse import Warehouse
from app.providers.base import RouteDetail, RouteLeg
from app.core.errors import RoutingProviderError
from app.api.v1.map_dashboard import _route_provider_failure
from tests.conftest import auth_headers


@pytest.fixture
async def a_location(session):
    location = ServiceLocation(
        service_code="SRV-API-01",
        location_name="Nearby Provisions",
        address="12 Test Road, Anna Nagar",
        area="Anna Nagar",
        city="Chennai",
        latitude=Decimal("13.086000"),
        longitude=Decimal("80.211000"),
        service_area="Anna Nagar",
    )
    session.add(location)
    await session.commit()
    return location


# --- Authentication -----------------------------------------------------


async def test_health_needs_no_auth(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_protected_endpoint_rejects_anonymous(client):
    response = await client.get("/api/v1/customers")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_route_preview_requires_authentication(client):
    response = await client.post(
        "/api/v1/routes/preview", json={"latitude": 13.08, "longitude": 80.27}
    )
    assert response.status_code == 401


async def test_stream_token_requires_access_token_and_returns_stream_scoped_token(
    client, ops_user
):
    anonymous = await client.post("/api/v1/auth/stream-token")
    assert anonymous.status_code == 401

    authenticated = await client.post(
        "/api/v1/auth/stream-token", headers=auth_headers(ops_user)
    )
    assert authenticated.status_code == 200, authenticated.text
    body = authenticated.json()
    assert body["streamToken"]
    assert body["expiresIn"] == 300


async def test_route_preview_rejects_out_of_range_coordinates(client, ops_user):
    response = await client.post(
        "/api/v1/routes/preview",
        headers=auth_headers(ops_user),
        json={"latitude": 95, "longitude": 80.27},
    )
    # The API's shared validation envelope uses 400 for request validation.
    assert response.status_code == 400
    assert "latitude" in response.text.lower()


def test_route_preview_error_preserves_safe_provider_detail():
    secret = "private-route-token-value"
    status_code, message = _route_provider_failure(RoutingProviderError(
        f"OSRM upstream failed while using Bearer {secret}", code="PROVIDER_HTTP_5XX"
    ))
    assert status_code == "ROUTE_UNAVAILABLE"
    assert "OSRM upstream failed" in message
    assert secret not in message


async def test_route_preview_uses_active_warehouse_and_returns_road_route(
    client, ops_user, warehouse, monkeypatch
):
    class StubRoadProvider:
        name = "osrm"

        async def get_driving_distances(self, origins, destination):
            return [RouteLeg(4200, 720, provider=self.name) for _ in origins]

        async def get_driving_routes(self, origin, destination):
            return [RouteDetail(
                distance_meters=4200,
                duration_seconds=720,
                geometry="_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                geometry_format="encoded_polyline_5",
                provider=self.name,
            )]

    monkeypatch.setattr(
        "app.api.v1.map_dashboard.get_route_preview_provider",
        lambda: StubRoadProvider(),
    )
    response = await client.post(
        "/api/v1/routes/preview",
        headers=auth_headers(ops_user),
        json={"latitude": 13.08, "longitude": 80.27},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nearestWarehouse"]["id"] == str(warehouse.id)
    assert body["nearestCustomer"] is None
    assert body["routes"]
    assert body["routes"][0]["distanceMeters"] > 0
    assert body["routes"][0]["availability"] == "CALCULATED_UNVERIFIED"
    assert body["routes"][0]["geometry"] == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert body["warehouseRouteStatus"] == "CALCULATED_UNVERIFIED"
    assert body["customerRouteStatus"] == "NO_NEARBY_LOCATION"


async def test_route_preview_selects_road_nearest_eligible_origins_and_skips_unreachable(
    client, ops_user, warehouse, session, monkeypatch
):
    closer_by_road = Warehouse(
        warehouse_code="WH-TEST-02",
        warehouse_name="Road-nearest Warehouse",
        latitude=Decimal("13.0790000"),
        longitude=Decimal("80.2690000"),
        status=EntityStatus.ACTIVE,
    )
    customers = [
        Customer(
            customer_code=f"C-ROUTE-{index}", customer_name=f"Route Customer {index}",
            address="Test Road, Chennai", city="Chennai",
            latitude=Decimal(latitude), longitude=Decimal(longitude),
        )
        for index, (latitude, longitude) in enumerate(
            (("13.0200000", "80.2200000"), ("13.0795000", "80.2680000"), ("13.0700000", "80.2500000")),
            start=1,
        )
    ]
    session.add_all([closer_by_road, *customers])
    await session.commit()

    road_meters = {
        (13.01, 80.22): 2400,
        (13.079, 80.269): 8800,
        (13.02, 80.22): 1300,
        (13.0795, 80.268): 1900,
        (13.07, 80.25): None,
    }

    def distance_for(point):
        return road_meters.get((round(point.latitude, 4), round(point.longitude, 4)))

    class StubRoadProvider:
        name = "osrm"

        async def get_driving_distances(self, origins, destination):
            return [
                None if (meters := distance_for(point)) is None
                else RouteLeg(meters, 480, provider=self.name)
                for point in origins
            ]

        async def get_driving_routes(self, origin, destination):
            meters = distance_for(origin)
            assert meters is not None
            return [RouteDetail(
                distance_meters=meters, duration_seconds=480,
                geometry="_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                geometry_format="encoded_polyline_5", provider=self.name,
            )]

    monkeypatch.setattr(
        "app.api.v1.map_dashboard.get_route_preview_provider",
        lambda: StubRoadProvider(),
    )
    response = await client.post(
        "/api/v1/routes/preview",
        headers=auth_headers(ops_user),
        json={"latitude": 13.08, "longitude": 80.27, "searchRadiusKm": 25},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nearestWarehouse"]["id"] == str(warehouse.id)
    assert body["nearestWarehouse"]["distanceMeters"] == 2400
    assert body["nearestCustomer"]["id"] == str(customers[0].id)
    assert body["nearestCustomer"]["distanceMeters"] == 1300
    assert {route["origin"]["kind"] for route in body["routes"]} == {"WAREHOUSE", "CUSTOMER"}
    assert all(route["provider"] == "osrm" for route in body["routes"])


async def test_login_succeeds_and_returns_tokens(client, ops_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "ops", "password": "test-password-1234"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accessToken"] and body["refreshToken"]
    assert body["tokenType"] == "bearer"


async def test_login_failure_does_not_reveal_whether_user_exists(client, ops_user):
    wrong_password = await client.post(
        "/api/v1/auth/login", json={"username": "ops", "password": "wrong-password"}
    )
    no_such_user = await client.post(
        "/api/v1/auth/login", json={"username": "ghost", "password": "wrong-password"}
    )
    assert wrong_password.status_code == no_such_user.status_code == 401
    assert (
        wrong_password.json()["error"]["message"]
        == no_such_user.json()["error"]["message"]
    ), "Identical messages prevent username enumeration."


async def test_me_reports_role_and_permissions(client, ops_user):
    response = await client.get("/api/v1/auth/me", headers=auth_headers(ops_user))
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "OPERATIONS"
    assert "CHECK_RUN" in body["permissions"]
    assert "CONFIG_WRITE_THRESHOLD" not in body["permissions"]


# --- RBAC ---------------------------------------------------------------


async def test_view_only_cannot_create_customer(client, viewer_user):
    response = await client.post(
        "/api/v1/customers",
        headers=auth_headers(viewer_user),
        json={
            "customerCode": "CUST-VIEW-01",
            "customerName": "Should Fail",
            "address": "1 Test Road, Chennai",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_operations_cannot_change_threshold(client, ops_user):
    """The threshold is ADMIN-only (brief §21)."""
    response = await client.put(
        "/api/v1/admin/config/SERVICEABILITY_RADIUS_METERS",
        headers=auth_headers(ops_user),
        json={"value": "5000", "reason": "trying to widen coverage"},
    )
    assert response.status_code == 403


async def test_admin_can_change_threshold_and_it_is_audited(client, admin_user):
    response = await client.put(
        "/api/v1/admin/config/SERVICEABILITY_RADIUS_METERS",
        headers=auth_headers(admin_user),
        json={"value": "2500", "reason": "Board approved wider coverage for Q4 pilot"},
    )
    assert response.status_code == 200
    assert response.json()["value"] == "2500"

    history = await client.get(
        "/api/v1/admin/config/SERVICEABILITY_RADIUS_METERS/history",
        headers=auth_headers(admin_user),
    )
    entries = history.json()
    assert len(entries) == 1
    assert entries[0]["oldValue"] == "2000"
    assert entries[0]["newValue"] == "2500"
    assert entries[0]["reason"].startswith("Board approved")
    assert admin_user.username in entries[0]["changedByLabel"]


async def test_threshold_change_requires_a_reason(client, admin_user):
    response = await client.put(
        "/api/v1/admin/config/SERVICEABILITY_RADIUS_METERS",
        headers=auth_headers(admin_user),
        json={"value": "2500", "reason": ""},
    )
    assert response.status_code == 400


async def test_threshold_is_range_checked(client, admin_user):
    response = await client.put(
        "/api/v1/admin/config/SERVICEABILITY_RADIUS_METERS",
        headers=auth_headers(admin_user),
        json={"value": "0", "reason": "testing lower bound"},
    )
    assert response.status_code == 400


# --- Customer creation and the canonical response -----------------------


async def test_create_customer_runs_check_and_returns_canonical_shape(
    client, ops_user, a_location
):
    response = await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={
            "customerCode": "CUST-1001",
            "customerName": "ABC Foods",
            "phone": "+91 98400 12345",
            "address": "45 Second Avenue, Anna Nagar",
            "area": "Anna Nagar",
            "city": "Chennai",
            "pincode": "600040",
            "serviceType": "DAILY",
            "latitude": 13.0850,
            "longitude": 80.2101,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["customer"]["customerCode"] == "CUST-1001"
    result = body["serviceability"]
    assert result["distanceType"] == "ROAD_DISTANCE"
    assert result["thresholdMeters"] == 2000
    assert result["status"] in ("AVAILABLE", "NOT_AVAILABLE")
    assert result["nearestServiceLocation"]["serviceCode"] == "SRV-API-01"
    assert result["nearestServiceLocation"]["distanceKm"] == pytest.approx(
        result["nearestServiceLocation"]["distanceMeters"] / 1000, abs=0.001
    )
    assert result["checkId"]
    map_response = await client.get(
        "/api/v1/map/operations", headers=auth_headers(ops_user)
    )
    assert map_response.status_code == 200
    assert any(
        item["id"] == body["customer"]["id"]
        for item in map_response.json()["customers"]
    )


async def test_create_customer_with_manual_address_saves_trimmed_name(
    client, ops_user
):
    response = await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={
            "customerName": "  Manual Address Customer  ",
            "address": "45 Second Avenue, Anna Nagar, Chennai",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["customer"]["customerName"] == "Manual Address Customer"
    listed = await client.get(
        "/api/v1/customers", headers=auth_headers(ops_user), params={"q": "Manual Address Customer"}
    )
    assert listed.status_code == 200
    assert any(
        item["id"] == response.json()["customer"]["id"]
        for item in listed.json()["items"]
    )


@pytest.mark.parametrize("customer_name", ["", "   \t\n"])
async def test_create_customer_rejects_blank_or_whitespace_name(
    client, ops_user, customer_name
):
    response = await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={"customerName": customer_name, "address": "45 Test Road, Chennai"},
    )
    # Shared request-validation errors use the application's 400 envelope.
    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert any(
        detail.get("field") == "customerName"
        and "cannot be blank" in detail.get("message", "").lower()
        for detail in details
    )


async def test_admin_delete_removes_customer_but_preserves_audit_check(client, admin_user, a_location):
    response = await client.post(
        "/api/v1/customers",
        headers=auth_headers(admin_user),
        json={
            "customerCode": "CUST-DELETE-CHECK",
            "customerName": "Delete Check",
            "address": "45 Test Road, Chennai",
            "latitude": 13.0850,
            "longitude": 80.2101,
        },
    )
    assert response.status_code == 201
    check_id = response.json()["serviceability"]["checkId"]

    deleted = await client.delete(
        f"/api/v1/serviceability/checks/{check_id}",
        headers=auth_headers(admin_user),
    )
    assert deleted.status_code == 200
    assert deleted.json()["message"] == "Customer and its map marker deleted."

    preserved = await client.get(
        f"/api/v1/serviceability/checks/{check_id}",
        headers=auth_headers(admin_user),
    )
    assert preserved.status_code == 200
    assert preserved.json()["customerId"] is None

    customer = await client.get(
        "/api/v1/customers", headers=auth_headers(admin_user), params={"q": "CUST-DELETE-CHECK"}
    )
    assert customer.status_code == 200
    assert customer.json()["meta"]["total"] == 0


async def test_preview_does_not_create_a_duplicate_audit_row(client, admin_user, a_location):
    before = await client.get(
        "/api/v1/serviceability/checks", headers=auth_headers(admin_user)
    )
    preview = await client.post(
        "/api/v1/serviceability/preview",
        headers=auth_headers(admin_user),
        json={"latitude": 13.085, "longitude": 80.2101},
    )
    assert preview.status_code == 200

    after = await client.get(
        "/api/v1/serviceability/checks", headers=auth_headers(admin_user)
    )
    assert after.json()["meta"]["total"] == before.json()["meta"]["total"]


async def test_one_address_creates_one_persistent_check(client, admin_user, a_location):
    preview = await client.post(
        "/api/v1/serviceability/preview",
        headers=auth_headers(admin_user),
        json={"latitude": 13.085, "longitude": 80.2101},
    )
    assert preview.status_code == 200

    created = await client.post(
        "/api/v1/customers",
        headers=auth_headers(admin_user),
        json={
            "customerCode": "CUST-ONE-CHECK",
            "customerName": "One Check Customer",
            "address": "One Check Address, Chennai",
            "latitude": 13.085,
            "longitude": 80.2101,
        },
    )
    assert created.status_code == 201
    customer_id = created.json()["customer"]["id"]

    checks = await client.get(
        "/api/v1/serviceability/checks",
        headers=auth_headers(admin_user),
        params={"customer_id": customer_id},
    )
    assert checks.json()["meta"]["total"] == 1


async def test_duplicate_customer_code_is_rejected(client, ops_user, a_location):
    payload = {
        "customerCode": "CUST-DUP",
        "customerName": "First",
        "address": "1 Test Road, Chennai",
        "latitude": 13.085,
        "longitude": 80.2101,
    }
    first = await client.post(
        "/api/v1/customers", headers=auth_headers(ops_user), json=payload
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={**payload, "customerName": "Second"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DUPLICATE_RESOURCE"


async def test_customer_code_is_generated_when_omitted(client, ops_user, a_location):
    response = await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={
            "customerName": "Generated Code Customer",
            "address": "22 New Address, Chennai",
            "latitude": 13.085,
            "longitude": 80.2101,
        },
    )
    assert response.status_code == 201, response.text
    customer = response.json()["customer"]
    assert customer["customerCode"].startswith("CUST-")
    assert customer["address"] == "22 New Address, Chennai"


async def test_half_a_coordinate_is_rejected(client, ops_user):
    response = await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={
            "customerCode": "CUST-HALF",
            "customerName": "Half Coordinates",
            "address": "1 Test Road, Chennai",
            "latitude": 13.085,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_out_of_range_latitude_is_rejected(client, ops_user):
    response = await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={
            "customerCode": "CUST-BADLAT",
            "customerName": "Bad Latitude",
            "address": "1 Test Road, Chennai",
            "latitude": 95.0,
            "longitude": 80.2,
        },
    )
    assert response.status_code == 400


async def test_manual_location_update_recalculates(client, ops_user, a_location):
    created = await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={
            "customerCode": "CUST-MOVE",
            "customerName": "Movable Traders",
            "address": "1 Test Road, Chennai",
            "latitude": 13.2000,
            "longitude": 80.4000,
        },
    )
    customer_id = created.json()["customer"]["id"]

    moved = await client.patch(
        f"/api/v1/customers/{customer_id}/location",
        headers=auth_headers(ops_user),
        json={"latitude": 13.0855, "longitude": 80.2105, "reason": "Corrected on map"},
    )
    assert moved.status_code == 200
    body = moved.json()
    assert body["status"] == "AVAILABLE"
    assert body["nearestServiceLocation"]["serviceCode"] == "SRV-API-01"


# --- Nearby endpoint labelling ------------------------------------------


async def test_nearby_declares_itself_not_a_decision(client, ops_user, a_location):
    """Straight-line results must be unmistakable for a serviceability answer."""
    response = await client.get(
        "/api/v1/service-locations/nearby?lat=13.085&lng=80.2101&radius_m=8000",
        headers=auth_headers(ops_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["distanceType"] == "STRAIGHT_LINE"
    assert body["isServiceabilityDecision"] is False
    assert "road distance" in body["note"].lower()
    assert body["items"][0]["serviceCode"] == "SRV-API-01"


# --- Audit --------------------------------------------------------------


async def test_check_history_is_recorded_with_threshold(client, ops_user, a_location):
    await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={
            "customerCode": "CUST-AUDIT",
            "customerName": "Audited Foods",
            "address": "1 Test Road, Chennai",
            "latitude": 13.085,
            "longitude": 80.2101,
        },
    )
    response = await client.get(
        "/api/v1/serviceability/checks", headers=auth_headers(ops_user)
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 1

    check = items[0]
    assert check["thresholdMeters"] == 2000
    assert check["distanceType"] == "ROAD_DISTANCE"
    assert check["routingProvider"] == "fake"
    assert check["createdByLabel"].startswith("Test Operations")
    assert check["requestTimestamp"] and check["responseTimestamp"]


async def test_map_operations_returns_everything_needed(
    client, ops_user, a_location, warehouse
):
    response = await client.get(
        "/api/v1/map/operations", headers=auth_headers(ops_user)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["thresholdMeters"] == 2000
    assert body["center"]["latitude"] == pytest.approx(13.0827)
    assert len(body["warehouses"]) == 1
    assert len(body["serviceLocations"]) == 1
    assert body["tileUrl"].startswith("https://")


async def test_dashboard_metrics(client, ops_user, a_location):
    await client.post(
        "/api/v1/customers",
        headers=auth_headers(ops_user),
        json={
            "customerCode": "CUST-METRIC",
            "customerName": "Metric Foods",
            "address": "1 Test Road, Chennai",
            "latitude": 13.085,
            "longitude": 80.2101,
        },
    )
    response = await client.get(
        "/api/v1/dashboard/metrics", headers=auth_headers(ops_user)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["newCustomers"] >= 1
    assert body["thresholdMeters"] == 2000
    assert body["totalChecks"] >= 1


async def test_error_envelope_carries_request_id(client, ops_user):
    response = await client.get(
        "/api/v1/customers/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(ops_user),
    )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["requestId"]
    assert response.headers["X-Request-ID"] == error["requestId"]


async def test_security_headers_are_present(client):
    response = await client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"

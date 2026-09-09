# API Specification

Base path: `/api/v1`. All responses are JSON. All endpoints except `/auth/login` and `/health` require `Authorization: Bearer <jwt>`.

A machine-readable OpenAPI 3.1 document is served at `/api/v1/openapi.json`, with interactive docs at `/api/v1/docs` (disabled in production by `ENABLE_API_DOCS=false`).

---

## 1. Roles and permissions

| Permission | ADMIN | SUPERVISOR | OPERATIONS | SALES | VIEW_ONLY |
|---|:--:|:--:|:--:|:--:|:--:|
| View map, customers, checks, dashboard | ✅ | ✅ | ✅ | ✅ | ✅ |
| Create customer | ✅ | ✅ | ✅ | ✅ | ❌ |
| Run serviceability check | ✅ | ✅ | ✅ | ✅ | ❌ |
| Adjust customer location manually | ✅ | ✅ | ✅ | ❌ | ❌ |
| Create / edit service locations | ✅ | ✅ | ✅ | ❌ | ❌ |
| Import service locations | ✅ | ✅ | ❌ | ❌ | ❌ |
| Override decision → `PENDING_REVIEW` | ✅ | ✅ | ❌ | ❌ | ❌ |
| Read configuration | ✅ | ✅ | ✅ | ❌ | ❌ |
| **Change serviceability threshold** | ✅ | ❌ | ❌ | ❌ | ❌ |
| Manage users | ✅ | ❌ | ❌ | ❌ | ❌ |
| Export reports | ✅ | ✅ | ✅ | ✅ | ❌ |

The threshold is deliberately ADMIN-only, per §21 of the brief. Every change writes a `config_audit` row with old value, new value, actor and reason; the reason is a required field.

---

## 2. Endpoints

### Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/login` | Exchange username + password for an access token and refresh token. Rate limited to 5 attempts / 15 min per IP+username. |
| `POST` | `/auth/refresh` | Exchange a refresh token for a new access token. |
| `POST` | `/auth/logout` | Revoke the refresh token. |
| `GET`  | `/auth/me` | Current user and role. |

### Customers

| Method | Path | Description |
|---|---|---|
| `POST` | `/customers` | Create a customer. Geocodes the address if `latitude`/`longitude` are omitted, then runs a serviceability check. |
| `GET` | `/customers` | Paginated list. Filters: `status`, `area`, `q`, `created_from`, `created_to`. |
| `GET` | `/customers/{id}` | Single customer with latest serviceability summary. |
| `PATCH` | `/customers/{id}` | Update non-location fields. |
| `PATCH` | `/customers/{id}/location` | **Manual map adjustment.** Body `{latitude, longitude, reason?}`. Sets `coordinate_source=MANUAL` and automatically re-runs the serviceability check (brief §14). |
| `POST` | `/customers/{id}/serviceability-check` | Force a fresh check. Body `{force_refresh?: bool}` bypasses the route cache. |
| `GET` | `/customers/{id}/serviceability` | Latest serviceability result, including route geometry. |

### Service locations

| Method | Path | Description |
|---|---|---|
| `GET` | `/service-locations` | Paginated list. Filters: `status`, `service_area`, `route_id`, `q`. |
| `POST` | `/service-locations` | Create one. |
| `GET` | `/service-locations/{id}` | Single. |
| `PATCH` | `/service-locations/{id}` | Update. **Moving a location invalidates the route cache for every pair involving it** (brief §25 test 10). |
| `DELETE` | `/service-locations/{id}` | Soft delete → `status=INACTIVE`. Historic checks are retained. |
| `GET` | `/service-locations/nearby` | Query `lat`, `lng`, `radius_m`, `limit`. **Straight-line candidate search only.** The response is explicitly labelled `"distance_type": "STRAIGHT_LINE"` and carries `"is_serviceability_decision": false` so no consumer can mistake it for a decision. |

### Serviceability

| Method | Path | Description |
|---|---|---|
| `GET` | `/serviceability/checks` | Audit history. Filters: `customer_id`, `result`, `from`, `to`, `created_by`. Paginated, newest first. |
| `GET` | `/serviceability/checks/{id}` | One audit record in full. |
| `POST` | `/serviceability/preview` | Run a check for arbitrary coordinates without creating a customer. For sales pre-qualification. Still audited, with `customer_id = null`. |

### Warehouses, routes

| Method | Path | Description |
|---|---|---|
| `GET`/`POST` | `/warehouses`, `/warehouses/{id}` | CRUD. |
| `GET`/`POST` | `/routes`, `/routes/{id}` | CRUD. |

### Geocoding (proxied — keeps keys server-side)

| Method | Path | Description |
|---|---|---|
| `GET` | `/geocoding/autocomplete` | Query `q`, optional `session_token`. Returns address suggestions biased to Chennai. |
| `POST` | `/geocoding/geocode` | Body `{address}` → `{latitude, longitude, formatted_address, confidence, partial_match}`. |
| `POST` | `/geocoding/reverse` | Body `{latitude, longitude}` → formatted address. Used after a marker drag. |

### Map

| Method | Path | Description |
|---|---|---|
| `GET` | `/map/operations` | One call that returns everything the map needs: warehouses, active service locations (viewport-bounded), recent customers with their status, and current threshold. Query: `bbox`, `since`, `limit`. |

### Dashboard and reports

| Method | Path | Description |
|---|---|---|
| `GET` | `/dashboard/metrics` | Today's counters — new customers, available, not available, verification required, routing errors, average distance, breakdown by area. Query `date` defaults to today in Asia/Kolkata. |
| `GET` | `/reports/serviceability` | Filterable report rows. |
| `GET` | `/reports/serviceability/export` | Query `format=csv\|xlsx\|pdf`. Streams a file. |

### Admin configuration

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/config` | All configuration keys with values, types and descriptions. |
| `PUT` | `/admin/config/{key}` | Body `{value, reason}`. ADMIN only for `requires_admin` keys. Audited. |
| `GET` | `/admin/config/{key}/history` | Change history for one key. |
| `GET`/`POST`/`PATCH` | `/admin/users` | User management. ADMIN only. |
| `GET` | `/admin/routing/usage` | Routing and geocoding API call counts, cache hit rate, error rate, by day. Cost monitoring per brief §19. |
| `POST` | `/admin/routing/health` | Live provider health probe. |

### Import

| Method | Path | Description |
|---|---|---|
| `POST` | `/imports` | Multipart upload of CSV or XLSX. Validates and geocodes; **does not commit**. Returns a batch id and validation report. |
| `GET` | `/imports/{id}` | Batch status and full validation report. |
| `GET` | `/imports/{id}/rows` | Row-level results with per-row errors. Filter `status`. |
| `POST` | `/imports/{id}/commit` | Commit only the `VALID` rows. Invalid rows are never committed. |
| `GET` | `/imports/{id}/report` | Download the validation report as CSV or XLSX. |

### Real-time

| Method | Path | Description |
|---|---|---|
| `GET` | `/stream` | Server-Sent Events. Events: `customer.created`, `check.started`, `check.completed`, `check.error`, `service_location.updated`, `config.updated`, `heartbeat` (every 15 s). Token passed as `?access_token=` because `EventSource` cannot set headers; the token is single-use-scoped to the stream. |

### Operations

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness. No auth. |
| `GET` | `/health/ready` | Readiness — checks Postgres, Redis and provider reachability. No auth. |
| `GET` | `/metrics` | Prometheus exposition. Restricted to the metrics network. |

---

## 3. Canonical serviceability response

`GET /api/v1/customers/{id}/serviceability` and the result of `POST .../serviceability-check`.

**Available:**

```json
{
  "customerId": "CUST-1001",
  "customerName": "ABC Foods",
  "checkId": "6f1c2f0e-2c5f-4f4a-9a4e-2d2c0b1f9a11",
  "status": "AVAILABLE",
  "thresholdMeters": 2000,
  "distanceType": "ROAD_DISTANCE",
  "routingProvider": "google_maps",
  "candidateCount": 7,
  "cacheHit": false,
  "nearestServiceLocation": {
    "id": "SRV-104",
    "serviceCode": "SRV-104",
    "name": "Existing Customer A",
    "latitude": 13.0854,
    "longitude": 80.2101,
    "serviceArea": "Anna Nagar",
    "routeCode": "RT-NORTH-02",
    "distanceMeters": 1450,
    "distanceKm": 1.45
  },
  "route": {
    "distanceMeters": 1450,
    "durationSeconds": 420,
    "geometry": "yzvmAmknvMHc@...",
    "geometryFormat": "encoded_polyline_5"
  },
  "marginMeters": 550,
  "calculatedAt": "2026-09-04T17:30:12+05:30",
  "durationMs": 812
}
```

**Not available** — identical shape; `route.geometry` is still returned so operations can see *why* the road distance is what it is:

```json
{
  "customerId": "CUST-1002",
  "status": "NOT_AVAILABLE",
  "thresholdMeters": 2000,
  "distanceType": "ROAD_DISTANCE",
  "nearestServiceLocation": {
    "id": "SRV-104",
    "name": "Existing Customer A",
    "distanceMeters": 3240,
    "distanceKm": 3.24
  },
  "marginMeters": -1240,
  "reason": "Customer is outside the existing service coverage based on road distance."
}
```

**Routing failure** — HTTP `200` with an error status, not `5xx`, because the check itself completed and was audited. The customer record is preserved and retry is offered:

```json
{
  "customerId": "CUST-1003",
  "status": "ROUTE_CALCULATION_ERROR",
  "thresholdMeters": 2000,
  "nearestServiceLocation": null,
  "errorCode": "ROUTING_PROVIDER_UNAVAILABLE",
  "message": "Unable to calculate driving distance. Please retry.",
  "retryable": true,
  "candidateCount": 6
}
```

**Geocoding failure:**

```json
{
  "customerId": "CUST-1004",
  "status": "LOCATION_VERIFICATION_REQUIRED",
  "errorCode": "GEOCODING_FAILED",
  "message": "We could not confirm this address. Please place the marker on the map and confirm the location.",
  "retryable": true,
  "suggestedCenter": { "latitude": 13.0827, "longitude": 80.2707 }
}
```

**No candidates in range** — distinct from `NOT_AVAILABLE` when there are no service locations at all:

```json
{
  "customerId": "CUST-1005",
  "status": "NO_SERVICE_LOCATION_CONFIGURED",
  "thresholdMeters": 2000,
  "candidateCount": 0,
  "message": "No active service locations are configured within the candidate search radius."
}
```

When service locations exist but none fall inside the Stage-1 radius, the result is `NOT_AVAILABLE` with `candidateCount: 0` and an explanatory `reason`, because that genuinely is a coverage answer. When the table has no active rows at all, the result is `NO_SERVICE_LOCATION_CONFIGURED`, because that is a configuration problem, not a coverage answer. `SERVICEABILITY_EMPTY_TABLE_RESULT` makes this configurable (brief §25 test 6).

---

## 4. Error envelope

Every 4xx/5xx uses one shape:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Latitude must be between -90 and 90.",
    "details": [{ "field": "latitude", "issue": "out_of_range" }],
    "requestId": "01JB8K3Q6M2T7C9V0F2H4X5Y6Z"
  }
}
```

`requestId` is generated per request, attached to every log line for that request, and returned in the `X-Request-ID` header. It is the primary handle for incident investigation.

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Request failed schema or business validation. |
| 401 | `UNAUTHENTICATED` | Missing/expired token. |
| 403 | `FORBIDDEN` | Role lacks the permission. |
| 404 | `NOT_FOUND` | Resource does not exist. |
| 409 | `DUPLICATE_RESOURCE` | `customer_code` / `service_code` already exists. |
| 422 | `UNPROCESSABLE` | Well-formed but semantically invalid. |
| 429 | `RATE_LIMITED` | Includes `Retry-After`. |
| 503 | `PROVIDER_UNAVAILABLE` | Only for provider health endpoints; serviceability checks return 200 + `ROUTE_CALCULATION_ERROR` instead. |

---

## 5. Rate limits

| Scope | Limit |
|---|---|
| Login | 5 / 15 min per IP + username |
| Serviceability check | 60 / min per user |
| Autocomplete | 120 / min per user (Google Places is billed per session) |
| Import upload | 10 / hour per user |
| Everything else | 300 / min per user |

Enforced with a Redis token bucket so limits hold across API replicas.

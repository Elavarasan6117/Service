# System Architecture — Chennai Serviceability Platform

**Version:** 1.0
**Owner:** Vendolite Operations Engineering
**Core business rule:** `ACTUAL ROAD DISTANCE <= SERVICEABILITY_RADIUS_METERS (default 2000)` → `AVAILABLE`, otherwise `NOT_AVAILABLE`.

---

## 1. Stated assumptions

These are assumptions, not facts. Each one is called out because a wrong assumption here changes the build.

| # | Assumption | Impact if wrong |
|---|---|---|
| A1 | Routing + geocoding provider is **Google Maps Platform** (Geocoding API, Places Autocomplete, Distance Matrix API, Directions API), with a billing-enabled project and a server-side API key. | Swap the provider adapter only. Nothing else changes — see §5. |
| A2 | Existing service locations will be supplied later. Today they exist only as **points on a map image** (green/yellow markers plus a warehouse marker). Coordinates must be digitised before go-live. | Section §9 defines the digitisation path. The importer accepts CSV/XLSX and geocodes rows with missing coordinates. |
| A3 | Single warehouse in Chennai to start; the schema supports many. | None — `warehouses` is already a table, not a config value. |
| A4 | All coordinates are WGS84 (EPSG:4326). Distance maths is done in **integer metres**. | None. |
| A5 | Deployment is Docker Compose on a single Linux host for DEV/QA/UAT. Production may move to K8s later; the images are unchanged. | Add manifests; the application is stateless apart from Postgres and Redis. |
| A6 | Expected volume: hundreds of serviceability checks/day, growing to 10,000+ service locations. | Candidate capping and cache TTL are configurable, not hard-coded. |
| A7 | No existing CRM/ERP integration in phase 1. Customers are created in this application. | An inbound adapter is added behind `ImportService`; the domain model does not change. |
| A8 | Users are internal Vendolite staff authenticated with username + password. No SSO in phase 1. | Add an OIDC provider behind the same `User` model and role mapping. |

---

## 2. Technology choices and why

| Layer | Choice | Reason |
|---|---|---|
| Database | **PostgreSQL 16 + PostGIS 3.4** | Stage-1 candidate search needs a real spatial index. PostGIS `<->` KNN operator over a GiST index returns the *k* nearest locations in sub-millisecond time at 10,000+ rows. No other choice in the stack does this without an extra service. |
| Backend | **Python 3.11 + FastAPI** | Async-native, which matters because a serviceability check fans out to an external HTTP API and must not block a worker. Pydantic gives request validation for free (mandatory per §20 of the brief). GeoAlchemy2 maps PostGIS types directly to the ORM. |
| ORM / migrations | SQLAlchemy 2.0 + GeoAlchemy2 + Alembic | Versioned, reversible migrations are a rollback requirement. |
| Cache / rate limit | **Redis 7** | Route-result cache (§19 cost control), token-bucket rate limiter, and SSE fan-out across multiple API workers. |
| Frontend | **React 18 + TypeScript + Vite** | Type safety against the generated API schema; fast dev loop. |
| Map | **Leaflet + OpenStreetMap tiles** | Deliberate: it means **no Google key is ever shipped to the browser**. Every Google call (geocode, autocomplete, distance matrix, directions) is proxied by the backend. Route geometry comes back as a decoded polyline and is drawn as a Leaflet layer. This directly satisfies "Do not expose routing API keys in frontend code". |
| Real-time | **Server-Sent Events over Redis pub/sub** | The dashboard is read-mostly and one-directional. SSE is simpler than WebSocket, survives proxies, and auto-reconnects. Multiple operations users each hold one SSE stream; events fan out through Redis so any API replica can publish. |
| Tests | pytest + pytest-asyncio + httpx | The 10 acceptance scenarios in §25 of the brief run against a fake routing provider so they are deterministic and cost nothing. |
| Metrics | prometheus-client | Scrape endpoint at `/metrics`; alert rules shipped in `deploy/prometheus/`. |

**Why not straight-line distance as the decision.** Haversine is used *only* to build the Stage-1 candidate set, and it is deliberately over-inclusive (default 4× the threshold). The value stored in `serviceability_checks.calculated_distance_meters` and compared against the threshold is always a road distance returned by the routing provider, tagged `distance_type = 'ROAD_DISTANCE'`. There is no code path that can decide `AVAILABLE`/`NOT_AVAILABLE` from a geodesic distance — see the guard in `ServiceabilityEngine._decide()`.

---

## 3. Component diagram

```mermaid
flowchart TB
    subgraph Browser["Operations Browser"]
        UI["React + TypeScript SPA"]
        MAP["Leaflet map — OSM tiles"]
        SSE_C["EventSource client"]
        UI --- MAP
        UI --- SSE_C
    end

    subgraph Edge["Edge"]
        NGINX["Nginx / TLS termination<br/>HTTPS, security headers, gzip"]
    end

    subgraph API["FastAPI application"]
        AUTH["Auth + RBAC<br/>JWT, 5 roles"]
        RL["Rate limiter<br/>Redis token bucket"]
        subgraph Domain["Domain services"]
            CUST["CustomerService"]
            ENGINE["ServiceabilityEngine<br/>source of truth"]
            SPATIAL["SpatialSearchService<br/>PostGIS KNN"]
            IMPORT["ImportService<br/>CSV / XLSX"]
            CONFIG["ConfigService<br/>threshold, audited"]
            AUDITSVC["AuditService"]
        end
        subgraph Ports["Provider ports — interfaces"]
            GEO_P["GeocodingProvider"]
            ROUTE_P["RoutingProvider"]
        end
        EVT["EventPublisher → SSE"]
    end

    subgraph Adapters["Provider adapters — swappable"]
        GMAP["GoogleMapsAdapter<br/>Geocoding · Places · DistanceMatrix · Directions"]
        OSRM["OsrmAdapter<br/>secondary / fallback"]
        FAKE["FakeAdapter<br/>tests only"]
    end

    subgraph Data["Stateful"]
        PG[("PostgreSQL 16 + PostGIS<br/>GiST spatial indexes")]
        REDIS[("Redis 7<br/>route cache · rate limit · pub-sub")]
    end

    subgraph Obs["Observability"]
        PROM["Prometheus /metrics"]
        LOGS["Structured JSON logs"]
    end

    GOOGLE(["Google Maps Platform"])

    UI -->|HTTPS REST| NGINX
    SSE_C -->|"SSE /api/v1/stream"| NGINX
    NGINX --> AUTH --> RL --> Domain
    ENGINE --> SPATIAL --> PG
    ENGINE --> ROUTE_P
    CUST --> GEO_P
    IMPORT --> GEO_P
    GEO_P --> GMAP
    ROUTE_P --> GMAP
    ROUTE_P -.fallback.-> OSRM
    GEO_P -.tests.-> FAKE
    GMAP -->|"server-side key"| GOOGLE
    ROUTE_P <-->|"cache read/write"| REDIS
    ENGINE --> AUDITSVC --> PG
    ENGINE --> EVT --> REDIS
    REDIS -->|pub/sub| SSE_C
    Domain --> PG
    API --> PROM
    API --> LOGS
    MAP -->|tiles| OSMT(["OpenStreetMap tiles"])
```

**The key security property visible in this diagram:** the browser talks only to Nginx. There is no arrow from `Browser` to `Google Maps Platform`. Map tiles come from OSM, which needs no key.

---

## 4. Serviceability sequence

```mermaid
sequenceDiagram
    actor Ops as Operations user
    participant UI as React SPA
    participant API as FastAPI
    participant GEO as GeocodingProvider
    participant DB as PostGIS
    participant CACHE as Redis
    participant RT as RoutingProvider
    participant SSE as SSE stream

    Ops->>UI: Types address
    UI->>API: GET /geocoding/autocomplete?q=...
    API->>GEO: autocomplete (server-side key)
    GEO-->>API: suggestions
    API-->>UI: suggestions
    Ops->>UI: Picks suggestion, submits customer
    UI->>API: POST /customers
    API->>GEO: geocode(address)
    alt geocoding fails or is ambiguous
        GEO-->>API: failure / low confidence
        API-->>UI: status LOCATION_VERIFICATION_REQUIRED
        Note over UI: NOT NOT_AVAILABLE.<br/>User drags marker, then confirms.
        Ops->>UI: Drag marker → Confirm location
        UI->>API: PATCH /customers/{id}/location
    else geocoded
        GEO-->>API: lat, lng, formatted_address
    end
    API->>DB: INSERT customer (geography point)
    API-->>UI: 201 Created, status CALCULATING
    API->>SSE: publish customer.created

    rect rgb(238,246,255)
    Note over API,RT: Serviceability check — backend is the source of truth
    API->>DB: Stage 1 — KNN candidates within<br/>haversine radius (threshold × factor), LIMIT k
    DB-->>API: candidate service locations
    API->>CACHE: lookup cached road distances for pairs
    CACHE-->>API: hits (skip routing)
    API->>RT: Stage 2 — road distance matrix for cache misses
    alt routing provider fails after retries
        RT-->>API: error
        API->>DB: INSERT check (result ROUTE_CALCULATION_ERROR)
        API->>SSE: publish check.error
        API-->>UI: ROUTE_CALCULATION_ERROR + Retry
        Note over UI: Never reported as NOT_AVAILABLE.
    else routing succeeded
        RT-->>API: distance_m, duration_s per candidate
        API->>CACHE: store distances with TTL
        API->>API: nearest = min(road distance)
        API->>API: decide: distance_m <= threshold_m ?
        API->>RT: directions(nearest) → route geometry
        API->>DB: UPDATE customer + INSERT serviceability_check (full audit)
        API->>SSE: publish check.completed
        SSE-->>UI: live push to every connected dashboard
        UI->>UI: Render status, nearest, distance, route polyline
    end
    end
```

---

## 5. Provider abstraction

Nothing in the domain layer imports the Google SDK. Two ports are defined in `app/providers/base.py`:

```python
class RoutingProvider(Protocol):
    name: str
    async def get_driving_distance(origin, destinations) -> list[RouteLeg | None]
    async def get_driving_route(origin, destination) -> RouteDetail   # with geometry
    async def health_check() -> bool

class GeocodingProvider(Protocol):
    name: str
    async def geocode(address, *, region_bias) -> GeocodeResult
    async def reverse_geocode(lat, lng) -> GeocodeResult
    async def autocomplete(query, *, session_token) -> list[AddressSuggestion]
```

Adapters implemented: `GoogleMapsAdapter` (primary), `OsrmAdapter` (secondary/fallback and self-host option), `FakeRoutingProvider` / `FakeGeocodingProvider` (tests). Selection is by environment variable `ROUTING_PROVIDER` / `GEOCODING_PROVIDER`, resolved through a registry in `app/providers/registry.py`. Changing provider is a config change and a restart — no code edit.

Every adapter is wrapped by `ResilientRoutingProvider`, which supplies timeout, bounded retry with exponential backoff and jitter, rate-limit (HTTP 429) handling, circuit breaking, Prometheus timing, and cache read/write. Adapters therefore stay thin and easy to add.

---

## 6. Two-stage performance design

```mermaid
flowchart LR
    A["New customer<br/>lat, lng"] --> B["Stage 1 — PostGIS<br/>ST_DWithin on geography<br/>+ KNN order by <->"]
    B --> C{"candidates<br/>found?"}
    C -->|no| D["NO_SERVICE_LOCATION_IN_RANGE<br/>0 routing calls"]
    C -->|yes| E["Cap at CANDIDATE_MAX_COUNT<br/>default 10, nearest first"]
    E --> F["Redis cache lookup<br/>per (customer_pt, service_pt) pair"]
    F --> G{"all cached?"}
    G -->|yes| I["0 routing calls"]
    G -->|no| H["Stage 2 — one Distance Matrix call<br/>1 origin × N destinations"]
    H --> I["Road distances in metres"]
    I --> J["nearest = argmin(road distance)"]
    J --> K{"nearest <= threshold_m ?"}
    K -->|yes| L["AVAILABLE"]
    K -->|no| M["NOT_AVAILABLE"]
    L --> N["Directions call → geometry<br/>1 call, only for the winner"]
    M --> N
```

Cost per check in the worst case: **1 geocode + 1 Distance Matrix element-batch + 1 Directions call**, regardless of whether there are 100 or 100,000 service locations. With a warm cache it is often **zero** routing calls.

Stage 1 radius is `threshold_m × CANDIDATE_RADIUS_FACTOR` (default 4.0 → 8 km for a 2 km threshold). The factor exists because road distance always exceeds straight-line distance; a detour ratio of 4 is generous for Chennai's grid and guarantees the true road-nearest location is inside the candidate set. It is configurable and audited.

---

## 7. Data flow of the audit record

Every check writes one immutable row to `serviceability_checks` containing: customer, customer coordinates at time of check, nearest service location, road distance in metres, `distance_type = 'ROAD_DISTANCE'`, routing provider name, route duration, **the threshold that was in force at that moment**, the result, request and response timestamps, the initiating user, the candidate count, and whether the result came from cache. Rows are never updated or deleted — the rollback runbook depends on this.

---

## 8. Environments

```mermaid
flowchart LR
    DEV["DEVELOPMENT<br/>compose + fake providers<br/>seeded data"] --> QA["TEST / QA<br/>compose + Google sandbox key<br/>anonymised data"]
    QA --> UAT["UAT<br/>production-like<br/>real Chennai locations<br/>ops user acceptance"]
    UAT --> PROD["PRODUCTION<br/>TLS, backups, monitoring,<br/>alerting, restricted key"]
```

Each environment has its own `.env` file, its own database, and its own Google API key with its own quota and IP restriction. No environment shares credentials. Nothing is developed against production.

---

## 9. Digitising the existing map image (assumption A2)

The existing service locations currently exist only as coloured markers on an operational map image. Until real coordinates are supplied, the system runs on generated Chennai seed data so it is demonstrable end to end. Three supported paths to real data, in order of accuracy:

1. **Export from the source system** that produced the map (Google My Maps → KML/CSV, or the CRM behind it). Highest fidelity — real coordinates, no re-derivation. Feed straight into the importer.
2. **Address list → geocode.** Supply a CSV of customer IDs, names and addresses with no coordinates. The importer geocodes each row via Google, flags low-confidence results, and produces a validation report for manual review before activation. This is the expected path.
3. **Manual pin placement.** For any location whose address does not geocode cleanly, an operations user places it on the map in the admin screen and confirms. Every manual placement is audited.

The colour coding on the image (green / yellow) maps to `service_locations.status` — the importer accepts a `status` column and the admin UI renders each status with a distinct marker **and a text label**, never colour alone.

---

## 10. Module boundaries

Per §33 of the brief, these are separate modules with no circular dependencies:

```
app/
  api/            HTTP layer only — no business logic
  core/           config, security, logging, metrics, errors
  db/             session, base, spatial helpers
  models/         SQLAlchemy models
  schemas/        Pydantic request/response contracts
  providers/      geocoding + routing ports and adapters
  services/       customer, spatial_search, serviceability engine,
                  config, import, audit, events, reporting
  workers/        background tasks
```

Dependency direction is strictly `api → services → (providers, models)`. The frontend contains **no serviceability logic**; it renders the backend's decision and nothing else.

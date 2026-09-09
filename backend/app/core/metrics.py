"""Prometheus metrics.

The metric set is chosen to answer the questions in brief §29 directly:
how fast are we, how often does routing fail, how much are we spending on the
routing API, and are the live dashboards still connected.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# --- HTTP ---------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests",
    ["method", "endpoint", "status_class"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# --- Providers ----------------------------------------------------------

provider_requests_total = Counter(
    "provider_requests_total",
    "Calls made to an external provider. This is the billing counter.",
    ["provider", "operation", "outcome"],  # outcome: success|error|timeout|rate_limited
)

provider_duration_seconds = Histogram(
    "provider_duration_seconds",
    "External provider latency",
    ["provider", "operation"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0),
)

provider_elements_total = Counter(
    "provider_elements_total",
    "Distance-matrix elements requested. Google bills per element, not per call.",
    ["provider"],
)

provider_circuit_state = Gauge(
    "provider_circuit_state",
    "Circuit breaker state: 0 closed, 1 half-open, 2 open",
    ["provider"],
)

# --- Cache --------------------------------------------------------------

route_cache_events_total = Counter(
    "route_cache_events_total",
    "Route cache hits and misses",
    ["event"],  # hit|miss|store|invalidate
)

# --- Serviceability -----------------------------------------------------

serviceability_checks_total = Counter(
    "serviceability_checks_total",
    "Serviceability checks by result",
    ["result"],
)

serviceability_duration_seconds = Histogram(
    "serviceability_duration_seconds",
    "End-to-end serviceability check duration",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0),
)

serviceability_candidates = Histogram(
    "serviceability_candidates",
    "Stage-1 candidate count per check",
    buckets=(0, 1, 2, 3, 5, 8, 10, 20, 50),
)

serviceability_distance_meters = Histogram(
    "serviceability_distance_meters",
    "Road distance to the nearest service location",
    buckets=(250, 500, 1000, 1500, 2000, 3000, 5000, 10000, 20000),
)

# --- Geocoding ----------------------------------------------------------

geocoding_results_total = Counter(
    "geocoding_results_total",
    "Geocoding outcomes",
    ["outcome"],  # success|partial|no_result|error
)

# --- Real-time ----------------------------------------------------------

sse_connections = Gauge(
    "sse_connections",
    "Currently connected dashboard SSE streams",
)

sse_events_published_total = Counter(
    "sse_events_published_total",
    "Events published to the live stream",
    ["event_type"],
)

# --- Import -------------------------------------------------------------

import_rows_total = Counter(
    "import_rows_total",
    "Import rows processed by outcome",
    ["status"],
)

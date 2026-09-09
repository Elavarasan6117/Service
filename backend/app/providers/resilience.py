"""Resilience decorator applied to every provider.

Adapters stay thin and provider-specific; timeout, retry, circuit breaking,
metrics and cost accounting live here so they are identical no matter which
provider is configured, and so adding a provider cannot accidentally omit them.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Awaitable, Callable, TypeVar

import httpx

from app.core.config import settings
from app.core.errors import (
    CircuitOpenError,
    ProviderError,
    RoutingProviderError,
    RoutingRateLimitError,
    RoutingTimeoutError,
)
from app.core.logging import get_logger
from app.core.metrics import (
    provider_circuit_state,
    provider_duration_seconds,
    provider_elements_total,
    provider_requests_total,
)
from app.providers.base import (
    AddressSuggestion,
    Coordinate,
    GeocodeResult,
    RouteDetail,
    RouteLeg,
)

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitBreaker:
    """Stops hammering a provider that is already failing.

    Without this, a Google outage turns every serviceability check into
    ``max_retries`` doomed requests, multiplying latency and (for paid APIs)
    cost, while operations users stare at spinners. Open-circuit failures are
    immediate and still produce ROUTE_CALCULATION_ERROR, which is the correct,
    honest answer.
    """

    CLOSED, HALF_OPEN, OPEN = 0, 1, 2

    def __init__(self, name: str, fail_threshold: int, reset_seconds: int) -> None:
        self.name = name
        self.fail_threshold = fail_threshold
        self.reset_seconds = reset_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> int:
        if self._opened_at is None:
            return self.CLOSED
        if time.monotonic() - self._opened_at >= self.reset_seconds:
            return self.HALF_OPEN
        return self.OPEN

    async def before_call(self) -> None:
        state = self.state
        provider_circuit_state.labels(provider=self.name).set(state)
        if state == self.OPEN:
            raise CircuitOpenError()

    async def on_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._opened_at = None
        provider_circuit_state.labels(provider=self.name).set(self.CLOSED)

    async def on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._failures >= self.fail_threshold:
                self._opened_at = time.monotonic()
                logger.error(
                    "circuit_opened",
                    provider=self.name,
                    failures=self._failures,
                    reset_seconds=self.reset_seconds,
                )
        provider_circuit_state.labels(provider=self.name).set(self.state)


async def _call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    provider: str,
    operation: str,
    breaker: CircuitBreaker,
) -> T:
    await breaker.before_call()

    attempts = max(1, settings.PROVIDER_MAX_RETRIES)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                fn(), timeout=settings.PROVIDER_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            last_error = RoutingTimeoutError()
            outcome = "timeout"
            retryable = True
        except RoutingRateLimitError as exc:
            last_error = exc
            outcome = "rate_limited"
            retryable = True
        except httpx.TimeoutException:
            last_error = RoutingTimeoutError()
            outcome = "timeout"
            retryable = True
        except httpx.HTTPError as exc:
            last_error = RoutingProviderError(f"{provider} transport error: {exc}")
            outcome = "error"
            retryable = True
        except ProviderError as exc:
            last_error = exc
            outcome = "error"
            # Configuration and request-shape errors will fail identically on
            # retry; retrying them only wastes time and quota.
            retryable = exc.code not in (
                "PROVIDER_NOT_CONFIGURED",
                "PROVIDER_REQUEST_DENIED",
                "PROVIDER_INVALID_REQUEST",
                "PROVIDER_NO_ROUTE",
            )
        else:
            elapsed = time.perf_counter() - start
            provider_duration_seconds.labels(
                provider=provider, operation=operation
            ).observe(elapsed)
            provider_requests_total.labels(
                provider=provider, operation=operation, outcome="success"
            ).inc()
            await breaker.on_success()
            return result

        elapsed = time.perf_counter() - start
        provider_duration_seconds.labels(
            provider=provider, operation=operation
        ).observe(elapsed)
        provider_requests_total.labels(
            provider=provider, operation=operation, outcome=outcome
        ).inc()

        if not retryable or attempt == attempts:
            break

        # Exponential backoff with full jitter, so a fleet of API workers
        # recovering from an outage does not retry in lockstep.
        base = settings.PROVIDER_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        delay = random.uniform(0, base)
        logger.warning(
            "provider_retry",
            provider=provider,
            operation=operation,
            attempt=attempt,
            max_attempts=attempts,
            delay_seconds=round(delay, 3),
            error=str(last_error),
        )
        await asyncio.sleep(delay)

    await breaker.on_failure()
    assert last_error is not None
    raise last_error


class ResilientRoutingProvider:
    """Wraps any RoutingProvider with timeout, retry, circuit breaking, metrics."""

    def __init__(self, inner: Any, fallback: Any | None = None) -> None:
        self.inner = inner
        self.fallback = fallback
        self.name = inner.name
        self._breaker = CircuitBreaker(
            inner.name,
            settings.PROVIDER_CIRCUIT_FAIL_THRESHOLD,
            settings.PROVIDER_CIRCUIT_RESET_SECONDS,
        )
        self._fallback_breaker = (
            CircuitBreaker(
                fallback.name,
                settings.PROVIDER_CIRCUIT_FAIL_THRESHOLD,
                settings.PROVIDER_CIRCUIT_RESET_SECONDS,
            )
            if fallback
            else None
        )

    async def get_driving_distance(
        self, origin: Coordinate, destinations: list[Coordinate]
    ) -> list[RouteLeg | None]:
        if not destinations:
            return []
        # Google bills Distance Matrix per element, not per request. This is the
        # counter that maps to the invoice.
        provider_elements_total.labels(provider=self.name).inc(len(destinations))
        try:
            return await _call_with_retry(
                lambda: self.inner.get_driving_distance(origin, destinations),
                provider=self.name,
                operation="distance_matrix",
                breaker=self._breaker,
            )
        except ProviderError:
            if self.fallback is None:
                raise
            logger.warning(
                "routing_fallback_engaged",
                primary=self.name,
                fallback=self.fallback.name,
            )
            assert self._fallback_breaker is not None
            return await _call_with_retry(
                lambda: self.fallback.get_driving_distance(origin, destinations),
                provider=self.fallback.name,
                operation="distance_matrix",
                breaker=self._fallback_breaker,
            )

    async def get_driving_route(
        self, origin: Coordinate, destination: Coordinate
    ) -> RouteDetail:
        try:
            return await _call_with_retry(
                lambda: self.inner.get_driving_route(origin, destination),
                provider=self.name,
                operation="directions",
                breaker=self._breaker,
            )
        except ProviderError:
            if self.fallback is None:
                raise
            assert self._fallback_breaker is not None
            return await _call_with_retry(
                lambda: self.fallback.get_driving_route(origin, destination),
                provider=self.fallback.name,
                operation="directions",
                breaker=self._fallback_breaker,
            )

    async def health_check(self) -> bool:
        return await self.inner.health_check()

    async def close(self) -> None:
        await self.inner.close()
        if self.fallback is not None:
            await self.fallback.close()


class ResilientGeocodingProvider:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self._breaker = CircuitBreaker(
            f"{inner.name}_geocoding",
            settings.PROVIDER_CIRCUIT_FAIL_THRESHOLD,
            settings.PROVIDER_CIRCUIT_RESET_SECONDS,
        )

    async def geocode(self, address: str) -> GeocodeResult:
        return await _call_with_retry(
            lambda: self.inner.geocode(address),
            provider=self.name,
            operation="geocode",
            breaker=self._breaker,
        )

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodeResult:
        return await _call_with_retry(
            lambda: self.inner.reverse_geocode(lat, lng),
            provider=self.name,
            operation="reverse_geocode",
            breaker=self._breaker,
        )

    async def autocomplete(
        self, query: str, session_token: str | None = None
    ) -> list[AddressSuggestion]:
        return await _call_with_retry(
            lambda: self.inner.autocomplete(query, session_token),
            provider=self.name,
            operation="autocomplete",
            breaker=self._breaker,
        )

    async def health_check(self) -> bool:
        return await self.inner.health_check()

    async def close(self) -> None:
        await self.inner.close()

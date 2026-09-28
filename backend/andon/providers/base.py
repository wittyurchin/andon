"""Provider abstractions.

Swapping Open-Meteo for a paid weather API, or TomTom for HERE, means writing
one class here. Nothing above this layer changes.
"""

from __future__ import annotations

import abc
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..domain.enums import HealthStatus, SourceStatus
from ..domain.signals import (
    FetchContext,
    GeoPoint,
    IncidentSnapshot,
    ProviderResponse,
    RadarSnapshot,
    SourceRef,
    TrafficSnapshot,
    WeatherSnapshot,
)
from .errors import ProviderError

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# HTTP client errors quote the full request URL. For a keyed provider that URL
# contains the credential, and the error text goes to both the logs and the
# dashboard, so it is redacted at the single point where errors are captured.
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:key|api[-_]?key|apikey|token|access[-_]?token|subscription[-_]?key)=)[^&\s'\"]+"
)


def redact(text: str) -> str:
    return _SECRET_QUERY.sub(r"\1***", text)


class Provider(abc.ABC):
    """Common lifecycle for every external data source."""

    kind: str = "unknown"
    # True when requests carry a credential, so a 401/403/429 is a key problem.
    uses_key: bool = False

    @property
    @abc.abstractmethod
    def source(self) -> SourceRef: ...

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        """Report, without any network call, why this source cannot be used.

        ``None`` means "ready to call". Anything else means the registry records
        the status and never calls the provider — a missing key is not an
        outage and should not cost a request or a retry.
        """
        return None

    @abc.abstractmethod
    async def _fetch(self, point: GeoPoint, context: FetchContext):
        """Return the normalized snapshot, or raise."""

    async def fetch(
        self, point: GeoPoint, context: FetchContext | None = None
    ) -> ProviderResponse:
        """Fetch, never raise.

        A failed source must degrade the report, not break it, so failures are
        turned into an explicit ``UNAVAILABLE`` response with the reason kept.
        """
        context = context or FetchContext()
        blocked = self.precheck()
        if blocked is not None:
            status, details = blocked
            return self._failure(status, details.get("reason", status.value), 0, details)

        started = time.perf_counter()
        try:
            data = await self._fetch(point, context)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary
            latency = int((time.perf_counter() - started) * 1000)
            health, details = _classify(exc)
            reason = redact(f"{type(exc).__name__}: {exc}".strip())
            if not self.uses_key:
                details.pop("key_problem", None)
            problem = details.get("key_problem")
            if problem:
                # A bad key or an exhausted plan will not fix itself on retry:
                # say so plainly, first, and log it as an error.
                reason = f"{KEY_PROBLEM_TEXT[problem]} ({reason})"
            (log.error if problem else log.warning)(
                "provider key problem" if problem else "provider fetch failed",
                extra={
                    "provider": self.source.id,
                    "kind": self.kind,
                    "latency_ms": latency,
                    "health": health.value,
                    "key_problem": problem,
                    "error": reason,
                },
            )
            return self._failure(health, reason, latency, details)

        latency = int((time.perf_counter() - started) * 1000)
        partial = getattr(data, "failed_probes", None) or []
        log.info(
            "provider fetch ok",
            extra={
                "provider": self.source.id,
                "kind": self.kind,
                "latency_ms": latency,
                "partial_failures": len(partial),
            },
        )
        return ProviderResponse(
            source=self.source,
            status=SourceStatus.DEGRADED if partial else SourceStatus.OK,
            fetched_at=utcnow(),
            data=data,
            latency_ms=latency,
            health_hint=HealthStatus.DEGRADED if partial else HealthStatus.HEALTHY,
            details={"failed_probes": partial} if partial else {},
        )

    def _failure(
        self, health: HealthStatus, reason: str, latency: int, details: dict[str, Any]
    ) -> ProviderResponse:
        return ProviderResponse(
            source=self.source,
            status=SourceStatus.UNAVAILABLE,
            fetched_at=utcnow(),
            data=None,
            error=reason[:300],
            latency_ms=latency,
            health_hint=health,
            details={k: v for k, v in details.items() if k != "reason"},
        )

    async def aclose(self) -> None:
        return None


# Failures that mean "the credential or the plan", not "the service is down".
# They are never masked by a mock: the source stays in place and reports this.
KEY_PROBLEM_TEXT = {
    "rejected": "API key rejected — invalid, expired, or not enabled for this API",
    "limit_reached": "API usage limit reached for this key",
}


def _classify(exc: Exception) -> tuple[HealthStatus, dict[str, Any]]:
    if isinstance(exc, ProviderError):
        details = dict(exc.details)
        if exc.health is HealthStatus.UNAUTHORIZED:
            details.setdefault("key_problem", "rejected")
        elif details.get("quota_exceeded"):
            details.setdefault("key_problem", "limit_reached")
        return exc.health, details
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return HealthStatus.UNAUTHORIZED, {"http_status": code, "key_problem": "rejected"}
        if code == 429:
            return HealthStatus.UNAVAILABLE, {"http_status": code, "key_problem": "limit_reached"}
        return HealthStatus.UNAVAILABLE, {"http_status": code}
    if isinstance(exc, httpx.TimeoutException):
        return HealthStatus.UNAVAILABLE, {"timeout": True}
    return HealthStatus.UNAVAILABLE, {}


class WeatherProvider(Provider):
    kind = "weather"

    @abc.abstractmethod
    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot: ...


class RadarProvider(Provider):
    kind = "radar"

    @abc.abstractmethod
    async def _fetch(self, point: GeoPoint, context: FetchContext) -> RadarSnapshot: ...


class TrafficProvider(Provider):
    kind = "traffic"

    @abc.abstractmethod
    async def _fetch(self, point: GeoPoint, context: FetchContext) -> TrafficSnapshot: ...


class IncidentProvider(Provider):
    kind = "incidents"

    @abc.abstractmethod
    async def _fetch(self, point: GeoPoint, context: FetchContext) -> IncidentSnapshot: ...


class DisabledProvider(Provider):
    """An adapter boundary for a source with no verified access path.

    It exists so the source appears in the health registry with the exact
    blocker, and so enabling it later is a matter of implementing ``_fetch``
    — not of redesigning anything.
    """

    def __init__(self, source: SourceRef, blocker: str, **details: Any) -> None:
        self._source = source
        self._blocker = blocker
        self._details = details
        self.kind = source.kind

    @property
    def source(self) -> SourceRef:
        return self._source

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]]:
        return HealthStatus.DISABLED, {"reason": self._blocker, **self._details}

    async def _fetch(self, point: GeoPoint, context: FetchContext):  # pragma: no cover
        raise AssertionError("disabled providers are never called")

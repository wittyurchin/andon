"""TTL cache in front of external providers.

A 1-minute auto-refresh should not become a 1-minute external API call rate.
Cached responses keep their original ``observed_at``, so the UI still shows the
true age of the data rather than the age of our copy of it.
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..domain.signals import FetchContext, GeoPoint, ProviderResponse
from ..geo import location_key
from .base import Provider

log = logging.getLogger(__name__)


class CachedProvider(Provider):
    """Wraps a provider with a per-location TTL cache and single-flight."""

    def __init__(self, inner: Provider, ttl_s: int) -> None:
        self._inner = inner
        self._ttl = max(0, ttl_s)
        self._entries: dict[str, tuple[float, ProviderResponse]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.kind = inner.kind

    @property
    def inner(self) -> Provider:
        return self._inner

    @property
    def source(self):
        return self._inner.source

    def precheck(self):
        return self._inner.precheck()

    async def _fetch(self, point: GeoPoint, context: FetchContext):  # pragma: no cover
        return await self._inner._fetch(point, context)

    def _key(self, point: GeoPoint, context: FetchContext | None) -> str:
        # ~110 m grid, plus the approach set: traffic sampled on a different
        # set of corridors is a different answer.
        base = location_key(point, precision=3)
        return f"{base}|{context.cache_key}" if context and context.approaches else base

    async def fetch(
        self, point: GeoPoint, context: FetchContext | None = None
    ) -> ProviderResponse:
        key = self._key(point, context)
        lock = self._locks.setdefault(key, asyncio.Lock())

        async with lock:
            hit = self._entries.get(key)
            if hit is not None:
                cached_at, response = hit
                if self._ttl and (time.monotonic() - cached_at) < self._ttl:
                    log.debug(
                        "provider cache hit",
                        extra={"provider": self.source.id, "key": key},
                    )
                    return response.model_copy(update={"cached": True})

            response = await self._inner.fetch(point, context)
            # Only cache successes; a failing source should be retried promptly.
            if response.ok:
                self._entries[key] = (time.monotonic(), response)
            return response

    def invalidate(self, point: GeoPoint | None = None) -> None:
        if point is None:
            self._entries.clear()
            return
        prefix = location_key(point, precision=3)
        for key in [k for k in self._entries if k.split("|", 1)[0] == prefix]:
            del self._entries[key]

    async def aclose(self) -> None:
        await self._inner.aclose()

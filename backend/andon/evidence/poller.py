"""Optional background polling (``ANDON_BACKGROUND_POLLING=true``).

Refreshes each registered restaurant's *evidence* on its own interval, so the
history keeps accumulating even when no dashboard is open. It never generates
a report — reasoning stays on demand, and the LLM is never called just
because a timer fired.

Off by default: an unattended poller calls external APIs around the clock.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .service import EvidenceService

log = logging.getLogger(__name__)


class EvidencePoller:
    def __init__(self, evidence: EvidenceService, tick_s: int = 15) -> None:
        self._evidence = evidence
        self._tick_s = max(1, tick_s)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="evidence-poller")
            log.info("background polling started", extra={"tick_s": self._tick_s})

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await self.poll_due()
            await asyncio.sleep(self._tick_s)

    async def poll_due(self, now: datetime | None = None) -> list[str]:
        """Refresh every restaurant whose interval has elapsed. Returns their ids."""
        now = now or datetime.now(timezone.utc)
        refreshed: list[str] = []
        for restaurant in self._evidence.store.list_restaurants():
            last = restaurant.last_refreshed_at
            if last is not None and (now - last).total_seconds() < restaurant.refresh_interval_seconds:
                continue
            try:
                await self._evidence.refresh(restaurant.id)
                refreshed.append(restaurant.id)
            except Exception:  # noqa: BLE001 - one restaurant must not stop the rest
                log.exception("background refresh failed", extra={"restaurant_id": restaurant.id})
        return refreshed

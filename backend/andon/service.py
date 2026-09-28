"""Orchestration: from evidence to a finished situation report.

    evidence refresh (providers → normalize → persist → health → changes)
        → situation rules → change detection → reasoning → report

The evidence layer (``evidence.service``) owns every provider call and all
persistence. This module only reasons over what one refresh produced, and
enforces the two cross-cutting concerns: one in-flight refresh per location,
and no LLM call unless the situation actually changed.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

from .config import Settings
from .domain.signals import GeoPoint
from .domain.situation import (
    NormalizedSituation,
    Restaurant,
    SituationReport,
    SituationResult,
    SituationSnapshot,
    SourceHealth,
)
from .domain.enums import ChangeType
from .engine import change, rules
from .engine.normalize import SituationBuilder, build_trend_items
from .evidence.models import ChangeEvent, EvidenceBundle
from .evidence.service import EvidenceService
from .evidence.store import EvidenceStore
from .geo import location_key
from .history import HistoryStore
from .providers.registry import ProviderRegistry, SignalBundle
from .reasoning.claude import ReasoningEngine

log = logging.getLogger(__name__)

HISTORY_WINDOW_MINUTES = 180


class SituationResponse(SituationResult):
    """The situation report plus the evidence bundle it was built from."""

    restaurant_id: str
    evidence: EvidenceBundle


class SituationService:
    def __init__(self, settings: Settings, store: EvidenceStore | None = None) -> None:
        self._settings = settings
        self.store = store or EvidenceStore(settings.database_path)
        self.providers = ProviderRegistry(settings)
        self.evidence = EvidenceService(settings, self.store, self.providers)
        self.reasoning = ReasoningEngine(settings)
        self.history = HistoryStore(self.store, max_snapshots=settings.history_max_snapshots)
        self._builder = SituationBuilder(settings)
        self._reports: dict[str, tuple[str, SituationReport]] = {}
        # The last response actually built from a poll, per restaurant — served
        # back verbatim by a non-polling request (a page load) instead of
        # touching any provider. Frozen on purpose: its ages and freshness
        # grades stay exactly as they were at that poll until the next one.
        self._last_response: dict[str, SituationResponse] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # -- public ----------------------------------------------------------

    async def get_situation(
        self,
        name: str,
        point: GeoPoint,
        *,
        force: bool = False,
        poll: bool = True,
        refresh_interval_seconds: int = 300,
    ) -> SituationResponse:
        restaurant = self.evidence.register(name, point, refresh_interval_seconds)
        return await self.situation_for(restaurant.id, force=force, poll=poll)

    async def situation_for(
        self, restaurant_id: str, *, force: bool = False, poll: bool = True
    ) -> SituationResponse:
        if not poll:
            cached = self._last_response.get(restaurant_id)
            if cached is not None:
                return cached
            # Nothing polled yet for this restaurant (first load ever, or a
            # process restart) — one poll is unavoidable to have anything to
            # show at all. Every later non-polling load reuses it.

        restaurant = self.store.get_restaurant(restaurant_id)
        if restaurant is None:
            raise KeyError(restaurant_id)
        key = location_key(restaurant.location)

        # One refresh per location at a time: two browser tabs pointed at the
        # same kitchen should cost one set of API calls, not two.
        async with self._locks[restaurant_id]:
            outcome = await self.evidence.refresh(restaurant_id, force=force)
            now = datetime.now(timezone.utc)

            prior_history = self.history.get(key, within_minutes=HISTORY_WINDOW_MINUTES)
            situation = self._builder.build(
                Restaurant(name=restaurant.name, location=restaurant.location),
                outcome.signals,
                prior_history,
                now=now,
            )
            situation.fingerprint = change.fingerprint(situation)

            report = await self._report_for(key, situation, prior_history, force=force, now=now)

            self.history.record(key, situation)
            history = self.history.get(key, within_minutes=HISTORY_WINDOW_MINUTES)

            level_change = self._level_change(restaurant_id, prior_history, situation, now)
            if level_change is not None:
                self.store.insert_changes([level_change])
                outcome.bundle.changes.insert(0, level_change)

            response = SituationResponse(
                situation=situation,
                report=report,
                trends=build_trend_items(situation),
                history=history,
                history_note=HistoryStore.summarize(history),
                sources=self._source_health(outcome.signals, now),
                restaurant_id=restaurant_id,
                evidence=outcome.bundle,
            )
            self._last_response[restaurant_id] = response
            return response

    @staticmethod
    def _level_change(restaurant_id, prior_history, situation: NormalizedSituation, now) -> ChangeEvent | None:
        """The rules engine's overall band moving is a change event too."""
        if not prior_history:
            return None
        previous = prior_history[-1].level
        current = situation.overall.level
        if previous == current:
            return None
        return ChangeEvent(
            id=f"level-{restaurant_id}-{now.isoformat()}",
            restaurant_id=restaurant_id,
            detected_at=now,
            change_type=ChangeType.SITUATION_LEVEL_CHANGED,
            category="situation",
            summary=f"Overall situation {previous.value} → {current.value}",
            previous={"level": previous.value},
            current={"level": current.value, "headline": situation.overall.headline},
            magnitude=current.rank - previous.rank,
        )

    async def aclose(self) -> None:
        await asyncio.gather(
            self.providers.aclose(), self.reasoning.aclose(), return_exceptions=True
        )
        self.store.close()

    def status(self) -> dict:
        return {
            "providers": {
                "weather": [p.source.model_dump() for p in self.providers.weather],
                "radar": [p.source.model_dump() for p in self.providers.radar],
                "traffic": [p.source.model_dump() for p in self.providers.traffic],
                "incidents": [p.source.model_dump() for p in self.providers.incidents],
                "inactive": [
                    {"source": p.source.model_dump(), "status": status.value, **details}
                    for p, status, details in self.providers.inactive
                ],
            },
            "llm": {
                "available": self.reasoning.available,
                "status": self.reasoning.status,
                "model": self._settings.llm_model if self.reasoning.available else None,
                "effort": self._settings.llm_effort if self.reasoning.available else None,
            },
            "cached_reports": len(self._reports),
            "mode": self._settings.mode,
            "database_path": self.store.path,
            "mock_scenario": self.providers.clock.scenario,
        }

    # -- internals -------------------------------------------------------

    async def _report_for(
        self,
        key: str,
        situation: NormalizedSituation,
        history: list[SituationSnapshot],
        *,
        force: bool,
        now: datetime,
    ) -> SituationReport:
        cached_fingerprint, cached_report = self._reports.get(key, (None, None))

        # A manual refresh always re-fetches external data, but only re-runs the
        # LLM if the picture moved or the last report has had time to go stale.
        force_llm = force and _report_age_s(cached_report, now) >= 60

        regenerate, reason = change.should_regenerate(
            current_fingerprint=situation.fingerprint,
            cached_fingerprint=cached_fingerprint,
            cached_report=cached_report,
            max_age_s=self._settings.llm_max_report_age_s,
            now=now,
            force=force_llm,
        )

        if not regenerate and cached_report is not None:
            log.info(
                "reusing situation report",
                extra={"location": key, "reason": reason, "generator": cached_report.generator},
            )
            return cached_report.model_copy(update={"reused": True, "reuse_reason": reason})

        log.info("generating situation report", extra={"location": key, "reason": reason})
        report = await self.reasoning.generate(situation, history)
        report.reused = False
        report.reuse_reason = reason
        self._reports[key] = (situation.fingerprint, report)
        return report

    def _source_health(self, bundle: SignalBundle, now: datetime) -> list[SourceHealth]:
        health: list[SourceHealth] = []
        for response in bundle.everything():
            observed = response.observed_at
            if observed is not None and observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age = int((now - observed).total_seconds()) if observed else None
            health.append(
                SourceHealth(
                    source=response.source,
                    status=response.status,
                    observed_at=observed,
                    fetched_at=response.fetched_at,
                    age_seconds=age,
                    freshness=rules.freshness_of(
                        age,
                        self._settings.freshness_fresh_s,
                        self._settings.freshness_recent_s,
                        self._settings.freshness_aging_s,
                    ),
                    cached=response.cached,
                    latency_ms=response.latency_ms,
                    error=response.error,
                )
            )
        return health


def _report_age_s(report: SituationReport | None, now: datetime) -> float:
    if report is None or report.generated_at is None:
        return float("inf")
    generated = report.generated_at
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return (now - generated).total_seconds()


"""The evidence service: one refresh, one bundle.

    fetch providers → normalize → persist → source health → detect changes
                                                           → evidence bundle

Polling and reasoning are separate. ``refresh`` touches providers and the
store and never calls an LLM; ``bundle`` only reads the store. Whatever reasons
over the situation consumes the bundle — it never calls a provider itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import Settings
from ..domain.enums import Applicability, HealthStatus, IncidentCategory, Severity
from ..domain.signals import FetchContext, GeoPoint
from ..geo import location_key
from ..providers.cache import CachedProvider
from ..providers.registry import ProviderRegistry, SignalBundle
from . import changes as change_detection
from .access_graph import AccessGraphBuilder, OSM_ATTRIBUTION, to_targets
from .extract import Extractor
from .freshness import FreshnessPolicy
from .health import update_health
from .models import (
    AccessGraph,
    Conflict,
    EvidenceBundle,
    IncidentEvidence,
    Observation,
    RefreshSummary,
    RestaurantRecord,
    TrendSummary,
)
from .store import EvidenceStore

log = logging.getLogger(__name__)

RANK = {s.value: s.rank for s in Severity}
CHANGE_WINDOW = timedelta(hours=2)
TREND_WINDOW_MIN = 90
PRUNE_EVERY = 50  # refreshes between retention sweeps


@dataclass
class RefreshOutcome:
    bundle: EvidenceBundle
    signals: SignalBundle
    graph: AccessGraph


def restaurant_id_for(point: GeoPoint) -> str:
    return "r_" + hashlib.sha1(location_key(point).encode()).hexdigest()[:10]


class EvidenceService:
    def __init__(
        self,
        settings: Settings,
        store: EvidenceStore,
        registry: ProviderRegistry,
        graphs: AccessGraphBuilder | None = None,
        clock=None,
    ) -> None:
        self._settings = settings
        self.store = store
        self.registry = registry
        self.policy = FreshnessPolicy(settings.freshness_policy, settings.source_freshness)
        self.graphs = graphs or AccessGraphBuilder(
            source=(
                "mock"
                if settings.mode == "mock" and settings.access_graph_source == "auto"
                and not settings.needs_real_roads
                else settings.access_graph_source
            ),
            radius_m=settings.access_graph_radius_m,
            max_approaches=settings.access_max_approaches,
            max_age_days=settings.access_graph_max_age_days,
            overpass_endpoints=[e.strip() for e in settings.overpass_endpoints.split(",") if e.strip()],
            configured_points=settings.configured_access_points,
        )
        self._extractor = Extractor(self.policy.stale_after)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._refreshes = 0

    # -- restaurants ------------------------------------------------------

    def register(self, name: str, point: GeoPoint, refresh_interval_seconds: int = 300) -> RestaurantRecord:
        rid = restaurant_id_for(point)
        now = self._clock()
        existing = self.store.get_restaurant(rid)
        record = RestaurantRecord(
            id=rid,
            name=name.strip() or (existing.name if existing else "Restaurant"),
            location=existing.location if existing else point,
            refresh_interval_seconds=refresh_interval_seconds,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            last_refreshed_at=existing.last_refreshed_at if existing else None,
        )
        return self.store.upsert_restaurant(record)

    async def ensure_graph(self, restaurant: RestaurantRecord, *, rebuild: bool = False) -> AccessGraph:
        graph = self.store.load_access_graph(restaurant.id)
        if rebuild or self.graphs.needs_rebuild(graph, self._clock()):
            fresh = await self.graphs.build(restaurant.id, restaurant.location)
            # Keep a real graph rather than replace it with a fallback.
            if graph is None or not (fresh.source == "radial" and graph.source not in ("radial", "mock")):
                graph = fresh
                self.store.save_access_graph(graph)
        return graph  # type: ignore[return-value]

    # -- refresh ------------------------------------------------------------

    async def refresh(self, restaurant_id: str, *, force: bool = False) -> RefreshOutcome:
        restaurant = self.store.get_restaurant(restaurant_id)
        if restaurant is None:
            raise KeyError(restaurant_id)

        async with self._locks[restaurant_id]:
            started = self._clock()
            graph = await self.ensure_graph(restaurant)
            context = FetchContext(restaurant_id=restaurant.id, approaches=to_targets(graph))
            signals = await self.registry.fetch_all(restaurant.location, context, bypass_cache=force)
            now = self._clock()

            extraction = self._extractor.extract(
                restaurant, graph, signals.weather, signals.radar, signals.traffic,
                signals.incidents, now,
            )
            backfilled = await self._backfill(restaurant, graph, context, signals, now)
            new = self.store.insert_observations(backfilled + extraction.observations)
            self._update_incidents(restaurant.id, extraction.incidents, now)

            previous_health = self.store.get_health(restaurant.id)
            health = update_health(restaurant.id, previous_health, signals.everything(), self.policy, now)
            self.store.save_health(health)
            self.store.mark_refreshed(restaurant.id, now)

            bundle = self.bundle(restaurant.id, now=now, graph=graph)
            previous_state = self.store.get_state(restaurant.id)
            current_state = change_detection.digest(bundle)
            events = change_detection.detect(
                restaurant.id, previous_state, current_state, now,
                traffic_index_delta=self._settings.change_traffic_index_delta,
                travel_time_pct=self._settings.change_travel_time_pct,
                approach_labels={a.id: a.label for a in graph.approaches},
            )
            self.store.insert_changes(events)
            self.store.save_state(restaurant.id, current_state, now)
            bundle.changes = self.store.list_changes(restaurant.id, since=now - CHANGE_WINDOW, limit=50)

            responses = signals.all()
            bundle.refresh = RefreshSummary(
                refresh_id=hashlib.sha1(f"{restaurant.id}|{now.isoformat()}".encode()).hexdigest()[:16],
                started_at=started,
                completed_at=self._clock(),
                providers_called=sum(1 for r in responses if not r.cached),
                providers_failed=sum(1 for r in responses if not r.ok),
                observations_new=new,
                observations_seen=len(extraction.observations),
                incidents_active=len([i for i in bundle.incidents if i.status == "active"]),
                changes_detected=len(events),
            )

            self._refreshes += 1
            if self._refreshes % PRUNE_EVERY == 0:
                self.store.prune(self._settings.retention_days, now)

            log.info("evidence refreshed", extra={
                "restaurant_id": restaurant.id,
                "observations_new": new,
                "changes": len(events),
                "failed": bundle.refresh.providers_failed,
            })
            return RefreshOutcome(bundle=bundle, signals=signals, graph=graph)

    async def _backfill(self, restaurant, graph, context, signals: SignalBundle, now) -> list[Observation]:
        """Seed history from providers that can supply it — mocks only.

        Real providers get history the honest way: by being polled over time.
        """
        out: list[Observation] = []
        for response in signals.traffic:
            if not response.ok or self.store.count_observations(restaurant.id, response.source.id):
                continue
            provider = self.registry.find(response.source.id)
            inner = provider.inner if isinstance(provider, CachedProvider) else provider
            backfill = getattr(inner, "backfill", None)
            if backfill is None or response.source.mode != "mock":
                continue
            for snapshot in await backfill(restaurant.location, context):
                out += self._extractor.traffic(restaurant, graph, response, snapshot, now)
        return out

    def _update_incidents(self, restaurant_id: str, seen: dict[str, list[IncidentEvidence]], now: datetime) -> None:
        """Lifecycle, not overwrite: first_seen is kept, last_seen advances.

        Only sources that *answered* can clear their incidents. A source that
        failed this time tells us nothing, so its incidents stay as they were.
        """
        existing = {i.id: i for i in self.store.get_incidents(restaurant_id)}
        updates: list[IncidentEvidence] = []
        for source_id, incidents in seen.items():
            current_ids = set()
            for incident in incidents:
                current_ids.add(incident.id)
                prior = existing.get(incident.id)
                first_seen = prior.first_seen if prior and prior.status == "active" else now
                updates.append(incident.model_copy(update={"first_seen": first_seen, "last_seen": now}))
            for prior in existing.values():
                if prior.source_id == source_id and prior.status == "active" and prior.id not in current_ids:
                    updates.append(prior.model_copy(update={"status": "cleared", "cleared_at": now}))
        self.store.save_incidents(updates)

    # -- read model ---------------------------------------------------------

    def bundle(self, restaurant_id: str, *, now: datetime | None = None, graph: AccessGraph | None = None) -> EvidenceBundle:
        restaurant = self.store.get_restaurant(restaurant_id)
        if restaurant is None:
            raise KeyError(restaurant_id)
        now = now or self._clock()
        graph = graph or self.store.load_access_graph(restaurant_id)

        observations = [self._age(o, now) for o in self.store.latest_observations(restaurant_id)]
        forecasts = [self._age(o, now) for o in self.store.latest_forecasts(restaurant_id, now - timedelta(minutes=30))]
        incidents = [
            self._age_incident(i, now) for i in self.store.get_incidents(restaurant_id)
            if i.status == "active" or (i.cleared_at and now - i.cleared_at <= CHANGE_WINDOW)
        ]
        health = sorted(self.store.get_health(restaurant_id).values(), key=lambda h: (h.source_kind, h.source_id))

        bundle = EvidenceBundle(
            restaurant=restaurant,
            generated_at=now,
            observations=observations,
            forecasts=forecasts,
            incidents=incidents,
            source_health=health,
            access_graph=graph,
            changes=self.store.list_changes(restaurant_id, since=now - CHANGE_WINDOW, limit=50),
        )
        bundle.conflicts = self._conflicts(bundle)
        bundle.trends = self._trends(restaurant_id, now)
        bundle.coverage = self._coverage(bundle, graph)
        bundle.attributions = self._attributions(bundle, graph)
        return bundle

    def history(self, restaurant_id: str, *, minutes: int, category: str | None = None,
                source_id: str | None = None, subject_id: str | None = None,
                kind: str | None = "observation") -> list[Observation]:
        now = self._clock()
        rows = self.store.observation_history(
            restaurant_id, since=now - timedelta(minutes=minutes), category=category,
            source_id=source_id, subject_id=subject_id, kind=kind,
        )
        return [self._age(o, now) for o in rows]

    def _age(self, observation: Observation, now: datetime) -> Observation:
        age = int((now - observation.observed_at).total_seconds())
        stale_after = observation.stale_after_seconds or self.policy.stale_after(
            observation.source_id, observation.category, observation.kind)
        return observation.model_copy(update={"freshness_seconds": age, "stale": age >= stale_after})

    def _age_incident(self, incident: IncidentEvidence, now: datetime) -> IncidentEvidence:
        age = int((now - incident.last_seen).total_seconds())
        return incident.model_copy(update={
            "freshness_seconds": age,
            "stale": age >= self.policy.stale_after(incident.source_id, "incident"),
        })

    # -- derived views --------------------------------------------------------

    def _conflicts(self, bundle: EvidenceBundle) -> list[Conflict]:
        conflicts: list[Conflict] = []

        # Precipitation at or near the restaurant, across observation and model.
        members = []
        for o in bundle.observations:
            relevant = o.spatial.relevance >= 0.5 and not o.stale
            if not relevant or not o.value.get("band"):
                continue
            if o.category == "weather.precipitation" or (o.category == "radar.reflectivity" and o.subject_id == "at_site"):
                members.append(o)
        for f in bundle.forecasts:
            if f.category == "weather.precipitation" and f.valid_at == f.observed_at and f.value.get("band") \
                    and f.spatial.relevance >= 0.5 and not f.stale:
                members.append(f)
        if len({m.source_id for m in members}) >= 2:
            bands = [RANK[m.value["band"]] for m in members]
            spread = max(bands) - min(bands)
            if spread >= 1:
                conflicts.append(Conflict(
                    category="precipitation",
                    subject="precipitation at or near the restaurant",
                    spread=spread,
                    summary="; ".join(f"{m.source_name} ({m.kind}) reports {m.value['band']}" for m in members),
                    members=[_member(m) for m in members],
                ))

        # Traffic on the same approach from different sources.
        by_approach: dict[str, list[Observation]] = defaultdict(list)
        for o in bundle.observations:
            if o.category == "traffic.flow" and o.value.get("band") and not o.stale and o.subject_id:
                by_approach[o.subject_id].append(o)
        labels = {a.id: a.label for a in (bundle.access_graph.approaches if bundle.access_graph else [])}
        for approach, group in by_approach.items():
            if len({g.source_id for g in group}) < 2:
                continue
            bands = [RANK[g.value["band"]] for g in group]
            if max(bands) - min(bands) >= 1:
                conflicts.append(Conflict(
                    category="traffic",
                    subject=labels.get(approach, approach),
                    spread=max(bands) - min(bands),
                    summary="; ".join(f"{g.source_name} reports {g.value['band']}" for g in group),
                    members=[_member(g) for g in group],
                ))
        return conflicts

    def _trends(self, restaurant_id: str, now: datetime) -> list[TrendSummary]:
        rows = self.store.observation_history(restaurant_id, since=now - timedelta(minutes=TREND_WINDOW_MIN))
        series: dict[tuple[str, str, str | None], list[Observation]] = defaultdict(list)
        for o in rows:
            if o.category in ("weather.precipitation", "traffic.flow", "traffic.corridor_eta") or \
                    (o.category == "radar.reflectivity" and o.subject_id == "at_site"):
                series[(o.category, o.source_id, o.subject_id)].append(o)

        trends: list[TrendSummary] = []
        for (category, source_id, subject), points in series.items():
            if len(points) < 2:
                continue
            first, last = points[0], points[-1]
            direction = "steady"
            if category == "traffic.flow" and first.value.get("congestion_index") is not None \
                    and last.value.get("congestion_index") is not None:
                delta = last.value["congestion_index"] - first.value["congestion_index"]
                if abs(delta) >= 0.1:
                    direction = "worsening" if delta > 0 else "improving"
            elif category == "traffic.corridor_eta" and first.value.get("travel_time_s") and last.value.get("travel_time_s"):
                pct = last.value["travel_time_s"] / first.value["travel_time_s"] - 1
                if abs(pct) >= 0.2:
                    direction = "worsening" if pct > 0 else "improving"
            elif first.value.get("band") and last.value.get("band"):
                delta = RANK[last.value["band"]] - RANK[first.value["band"]]
                if delta:
                    direction = "worsening" if delta > 0 else "improving"
            window = max(1, int((last.observed_at - first.observed_at).total_seconds() // 60))
            trends.append(TrendSummary(
                category=category, source_id=source_id, subject_id=subject, direction=direction,
                window_minutes=window, points=len(points),
                first=_trend_point(first), last=_trend_point(last),
            ))
        return trends

    def _coverage(self, bundle: EvidenceBundle, graph: AccessGraph | None) -> dict:
        health = {h.source_id: h for h in bundle.source_health}
        by_kind: dict[str, list] = defaultdict(list)
        for h in bundle.source_health:
            by_kind[h.source_kind].append(h)

        def inactive(kinds: tuple[str, ...]) -> list[dict]:
            return [
                {"source_id": h.source_id, "status": h.status.value,
                 "reason": h.error or h.details.get("reason"), "requires": h.details.get("requires")}
                for k in kinds for h in by_kind.get(k, []) if not h.status.usable
            ]

        precip_obs = [
            o for o in bundle.observations
            if (o.category == "weather.precipitation" or (o.category == "radar.reflectivity" and o.subject_id == "at_site"))
        ]
        current_obs = [o for o in precip_obs if not o.stale]
        local = [o for o in current_obs if o.spatial.applicability in (Applicability.AT_SITE, Applicability.LOCAL)]
        nearest = min((o.spatial.distance_m for o in current_obs if o.spatial.distance_m is not None), default=None)
        if local:
            statement = "Current precipitation is observed within 1 km of the restaurant."
        elif current_obs:
            statement = (f"No current precipitation observation within 1 km; the nearest is "
                         f"{nearest / 1000:.1f} km away." if nearest is not None else
                         "Current precipitation observations lack a location.")
        else:
            statement = "No current precipitation observation — only forecasts, if any."

        approaches = graph.approaches if graph else []
        observed = {o.subject_id for o in bundle.observations
                    if o.category in ("traffic.flow", "traffic.corridor_eta") and not o.stale}
        covered = set()
        for h in bundle.source_health:
            if h.source_kind == "incidents" and h.status.usable:
                covered.update(c for c in (h.details.get("covered_categories") or []))
        incident_sources = [h.source_id for h in by_kind.get("incidents", []) if h.status.usable]

        return {
            "weather": {
                "sources_usable": [h.source_id for k in ("weather", "radar") for h in by_kind.get(k, []) if h.status.usable],
                "sources_inactive": inactive(("weather", "radar")),
                "observation_sources": sorted({o.source_id for o in current_obs}),
                "forecast_sources": sorted({f.source_id for f in bundle.forecasts}),
                "has_local_observation": bool(local),
                "nearest_observation_m": nearest,
                "statement": statement,
            },
            "traffic": {
                "sources_usable": [h.source_id for h in by_kind.get("traffic", []) if h.status.usable],
                "sources_inactive": inactive(("traffic",)),
                "graph_source": graph.source if graph else None,
                "graph_is_approximation": bool(approaches) and all(a.is_approximation for a in approaches),
                "approaches_total": len(approaches),
                "approaches_observed": len([a for a in approaches if a.id in observed]),
                "approaches_unobserved": [a.label for a in approaches if a.id not in observed],
            },
            "incidents": {
                "sources_usable": incident_sources,
                "sources_inactive": inactive(("incidents",)),
                "active": len([i for i in bundle.incidents if i.status == "active"]),
                "categories_not_covered": sorted(c.value for c in IncidentCategory if c.value not in covered)
                if incident_sources else sorted(c.value for c in IncidentCategory),
            },
            "stale_sources": [h.source_id for h in health.values() if h.status is HealthStatus.STALE],
        }

    def _attributions(self, bundle: EvidenceBundle, graph: AccessGraph | None) -> list[str]:
        names = {o.source_id: o.source_name for o in [*bundle.observations, *bundle.forecasts]}
        out: list[str] = []
        providers = {p.source.id: p.source for p in self.registry.active}
        for source_id in names:
            ref = providers.get(source_id)
            if ref and ref.attribution and ref.attribution not in out:
                out.append(ref.attribution)
        if graph and graph.source == "osm":
            out.append(OSM_ATTRIBUTION)
        return out


def _member(o: Observation) -> dict:
    return {
        "observation_id": o.id,
        "source_id": o.source_id,
        "source_name": o.source_name,
        "kind": o.kind,
        "band": o.value.get("band"),
        "distance_m": o.spatial.distance_m,
        "relevance": o.spatial.relevance,
        "observed_at": o.observed_at.isoformat(),
    }


def _trend_point(o: Observation) -> dict:
    keep = ("band", "congestion_index", "travel_time_s", "dbz_min")
    return {"at": o.observed_at.isoformat(), **{k: o.value.get(k) for k in keep if o.value.get(k) is not None}}

"""TomTom incident-details provider.

Note the coverage gap this provider declares: TomTom reports *flooding* but has
no waterlogging category. The rules engine therefore treats waterlogging as
"not covered by this source" rather than "confirmed absent" — see
``covered_categories``.

Verified against documentation/tomtom/incident-details.html (2026-09-27):
``bbox`` is minLon,minLat,maxLon,maxLat; ``fields`` selects properties
including ``id``, ``iconCategory``, ``magnitudeOfDelay`` (0 unknown, 1 minor,
2 moderate, 3 major, 4 undefined), ``events``, ``startTime``, ``endTime``,
``from``, ``to``, ``length``, ``delay``, ``roadNumbers``,
``probabilityOfOccurrence``, ``numberOfReports``, ``lastReportTime``.
The previous request omitted ``id``, so every incident lost its source record
id; it is requested now.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from typing import Any

from ...domain.enums import Confidence, HealthStatus, IncidentCategory, Severity
from ...domain.signals import FetchContext, GeoPoint, Incident, IncidentSnapshot, SourceRef
from ...geo import bbox, bearing_label, haversine_km
from ..base import IncidentProvider

ENDPOINT = "https://api.tomtom.com/traffic/services/5/incidentDetails"

FIELDS = (
    "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,"
    "magnitudeOfDelay,events{description,code,iconCategory},startTime,endTime,"
    "from,to,length,delay,roadNumbers,probabilityOfOccurrence,numberOfReports,"
    "lastReportTime}}}"
)

ICON_CATEGORY: dict[int, IncidentCategory] = {
    1: IncidentCategory.ACCIDENT,
    2: IncidentCategory.HAZARD,  # fog
    3: IncidentCategory.HAZARD,  # dangerous conditions
    4: IncidentCategory.HAZARD,  # rain
    5: IncidentCategory.HAZARD,  # ice
    6: IncidentCategory.CONGESTION,
    # Documented as 7 = Lane Closed, 8 = Road Closed. Keeping them apart:
    # a lane closure is not a closed road.
    7: IncidentCategory.LANE_CLOSURE,
    8: IncidentCategory.ROAD_CLOSURE,
    9: IncidentCategory.CONSTRUCTION,
    10: IncidentCategory.HAZARD,  # wind
    11: IncidentCategory.FLOODING,
    14: IncidentCategory.HAZARD,  # broken down vehicle
}

# magnitudeOfDelay: 0 unknown, 1 minor, 2 moderate, 3 major, 4 undefined
DELAY_SEVERITY = {1: Severity.LOW, 2: Severity.MEDIUM, 3: Severity.HIGH}

# probabilityOfOccurrence is TomTom's own qualifier; the mapping onto our
# confidence levels is ours.
PROBABILITY_CONFIDENCE = {
    "certain": Confidence.HIGH,
    "probable": Confidence.MEDIUM,
    "risk_of": Confidence.LOW,
    "improbable": Confidence.LOW,
}

COVERED = [
    IncidentCategory.ACCIDENT,
    IncidentCategory.ROAD_CLOSURE,
    IncidentCategory.LANE_CLOSURE,
    IncidentCategory.CONSTRUCTION,
    IncidentCategory.CONGESTION,
    IncidentCategory.FLOODING,
    IncidentCategory.HAZARD,
]


class TomTomIncidentProvider(IncidentProvider):
    uses_key = True

    def __init__(self, api_key: str | None, radius_km: float = 5.0, timeout_s: float = 8.0) -> None:
        self._api_key = api_key
        self._radius_km = radius_km
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="tomtom-incidents",
            name="TomTom Traffic Incidents",
            kind="incidents",
            mode="live",
            attribution="TomTom Traffic API",
            docs_url="https://developer.tomtom.com/traffic-api/documentation/traffic-incidents/incident-details",
        )

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        if not self._api_key:
            return HealthStatus.MISCONFIGURED, {"reason": "set ANDON_TOMTOM_API_KEY", "requires": "credentials"}
        return None

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> IncidentSnapshot:
        min_lon, min_lat, max_lon, max_lat = bbox(point, self._radius_km)
        response = await self._client.get(
            ENDPOINT,
            params={
                "key": self._api_key,
                "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                "fields": FIELDS,
                "language": "en-GB",
                "timeValidityFilter": "present",
            },
        )
        response.raise_for_status()
        payload = response.json() or {}

        incidents: list[Incident] = []
        for index, raw in enumerate(payload.get("incidents") or []):
            parsed = self._parse_incident(index, raw, point)
            if parsed is not None:
                incidents.append(parsed)

        incidents.sort(key=lambda inc: (inc.distance_km if inc.distance_km is not None else 99))
        return IncidentSnapshot(
            observed_at=datetime.now(timezone.utc),
            incidents=incidents,
            radius_km=self._radius_km,
            covered_categories=COVERED,
        )

    def _parse_incident(self, index: int, raw: dict, origin: GeoPoint) -> Incident | None:
        props = raw.get("properties") or {}
        events = props.get("events") or []
        description = next(
            (e.get("description") for e in events if e.get("description")),
            None,
        )
        if not description:
            description = "Traffic incident"

        category = ICON_CATEGORY.get(int(props.get("iconCategory") or 0), IncidentCategory.OTHER)
        nearest = _nearest_point(raw.get("geometry") or {}, origin)

        road = props.get("from") or None
        roads = props.get("roadNumbers") or []
        if roads:
            road = f"{road} ({', '.join(roads)})" if road else ", ".join(roads)

        geometry = raw.get("geometry") or None
        return Incident(
            # No documented id means no stable identity: say so rather than
            # inventing one that would look like the provider's.
            id=str(props["id"]) if props.get("id") else f"unidentified:{index}",
            category=category,
            description=description,
            distance_km=round(haversine_km(origin, nearest), 2) if nearest else None,
            bearing=bearing_label(origin, nearest) if nearest else None,
            road=road,
            severity_hint=DELAY_SEVERITY.get(int(props.get("magnitudeOfDelay") or 0)),
            reported_at=_parse_ts(props.get("startTime")),
            last_reported_at=_parse_ts(props.get("lastReportTime")),
            ends_at=_parse_ts(props.get("endTime")),
            confidence=PROBABILITY_CONFIDENCE.get(
                str(props.get("probabilityOfOccurrence") or "").lower(), Confidence.MEDIUM
            ),
            location=nearest,
            geometry=geometry if isinstance(geometry, dict) else None,
            delay_s=_num(props.get("delay")),
            length_m=_num(props.get("length")),
            attributes={
                k: props[k]
                for k in ("magnitudeOfDelay", "probabilityOfOccurrence", "numberOfReports", "to")
                if props.get(k) is not None
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _nearest_point(geometry: dict, origin: GeoPoint) -> GeoPoint | None:
    coords = geometry.get("coordinates")
    if not coords:
        return None

    flat: list[GeoPoint] = []

    def walk(node) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) == 2
            and all(isinstance(v, (int, float)) for v in node)
        ):
            lon, lat = node  # GeoJSON order
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                flat.append(GeoPoint(lat=float(lat), lon=float(lon)))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(coords)
    if not flat:
        return None
    return min(flat, key=lambda p: haversine_km(origin, p))


def _num(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

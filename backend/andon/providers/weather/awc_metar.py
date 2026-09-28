"""METAR weather provider (NOAA Aviation Weather Center).

Genuine station observations: a trained observer or certified automated station
at a known location, not a model. Free, no credentials.

Two properties shape how it is used:

* **Intensity is qualitative.** METAR reports ``-RA`` / ``RA`` / ``+RA``, not
  millimetres per hour. That maps directly onto the severity bands, so the
  provider sets ``rain_intensity`` and leaves ``precipitation_mm_h`` as
  ``None`` rather than inventing a rate.
* **Stations are sparse.** In India these are airports — the nearest one may be
  tens of kilometres from the restaurant. The distance is reported on every
  snapshot so the engine can discount confidence and the UI can show it.

There is no forecast here, so the outlook stays empty and the rain trend comes
from the station's own observation history.

Verified against documentation/noaa-awc/openapi.yaml (2026-09-27): ``bbox`` is
``lat0,lon0,lat1,lon1``; ``hours`` is "hours back to search". Rate limit is
100 requests/minute with a custom user agent (data_api.html).
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ...domain.enums import EvidenceKind, Severity
from ...domain.signals import FetchContext, GeoPoint, PrecipPoint, SourceRef, WeatherSnapshot
from ...geo import bbox, haversine_km
from ..base import WeatherProvider

ENDPOINT = "https://aviationweather.gov/api/data/metar"

KNOTS_TO_KMH = 1.852
STATUTE_MILE_M = 1609.34

# How much history to pull, and therefore how far back the trend can look.
HISTORY_HOURS = 3
# Search radius for a station. Beyond this the reading says more about the
# airport than about the restaurant.
SEARCH_RADIUS_KM = 120.0

# METAR precipitation types. Obscurations (BR, HZ, FU) are not precipitation
# and must not be read as rain — they affect visibility instead.
PRECIPITATION_CODES = frozenset(
    {"DZ", "RA", "SN", "SG", "IC", "PL", "GR", "GS", "UP"}
)

CODE_LABELS = {
    "DZ": "drizzle",
    "RA": "rain",
    "SN": "snow",
    "SG": "snow grains",
    "PL": "ice pellets",
    "GR": "hail",
    "GS": "small hail",
    "UP": "unknown precipitation",
    "BR": "mist",
    "FG": "fog",
    "HZ": "haze",
    "FU": "smoke",
    "DU": "dust",
    "SA": "sand",
    "SQ": "squalls",
    "FC": "funnel cloud",
}


class AwcMetarWeatherProvider(WeatherProvider):
    def __init__(self, timeout_s: float = 8.0, search_radius_km: float = SEARCH_RADIUS_KM) -> None:
        self._search_radius_km = search_radius_km
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            # The AWC docs ask for a custom user agent to avoid being filtered.
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="awc-metar",
            name="NOAA Aviation Weather Center (METAR)",
            kind="weather",
            mode="live",
            attribution="METAR station observations via NOAA AWC",
            docs_url="https://aviationweather.gov/data/api/",
        )

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot:
        min_lon, min_lat, max_lon, max_lat = bbox(point, self._search_radius_km)
        response = await self._client.get(
            ENDPOINT,
            params={
                # AWC takes bbox as minLat,minLon,maxLat,maxLon.
                "bbox": f"{min_lat},{min_lon},{max_lat},{max_lon}",
                "format": "json",
                "hours": HISTORY_HOURS,
            },
        )
        response.raise_for_status()
        reports = response.json() or []
        if not reports:
            raise RuntimeError(
                f"no METAR station reporting within {self._search_radius_km:.0f} km"
            )
        return self._build(reports, point)

    def _build(self, reports: list[dict], point: GeoPoint) -> WeatherSnapshot:
        located = [r for r in reports if r.get("lat") is not None and r.get("lon") is not None]
        if not located:
            raise RuntimeError("METAR reports carried no station coordinates")

        nearest_id, distance_km = min(
            (
                (
                    r["icaoId"],
                    haversine_km(point, GeoPoint(lat=float(r["lat"]), lon=float(r["lon"]))),
                )
                for r in located
                if r.get("icaoId")
            ),
            key=lambda pair: pair[1],
        )

        series = sorted(
            (r for r in located if r.get("icaoId") == nearest_id),
            key=lambda r: _observed_at(r) or datetime.min.replace(tzinfo=timezone.utc),
        )
        if not series:
            raise RuntimeError("nearest station returned no usable reports")

        latest = series[-1]
        observed_at = _observed_at(latest) or datetime.now(timezone.utc)
        intensity, condition = parse_weather(latest.get("wxString"))
        visibility_m = parse_visibility(latest.get("visib"))

        # The station's own past reports — real observations, so the trend they
        # support is observed rather than forecast.
        recent = [
            PrecipPoint(
                at=at,
                precipitation_mm_h=None,
                rain_intensity=parse_weather(report.get("wxString"))[0],
                condition=parse_weather(report.get("wxString"))[1],
                raw_text=report.get("rawOb"),
                visibility_m=parse_visibility(report.get("visib")),
                # A gust is only reported when there is one. Sustained wind is
                # a different quantity and must not be passed off as a gust.
                wind_gust_kmh=_knots_to_kmh(report.get("wgst")),
                kind=EvidenceKind.OBSERVED,
            )
            for report in series
            if (at := _observed_at(report)) is not None
        ]

        return WeatherSnapshot(
            observed_at=observed_at,
            precipitation_mm_h=None,  # METAR has no rate; never invent one
            rain_intensity=intensity,
            station_id=nearest_id,
            station_name=_station_label(latest, nearest_id),
            station_location=GeoPoint(lat=float(latest["lat"]), lon=float(latest["lon"])),
            station_distance_km=round(distance_km, 1),
            raw_text=latest.get("rawOb"),
            wind_speed_kmh=_knots_to_kmh(latest.get("wspd")),
            wind_gust_kmh=_knots_to_kmh(latest.get("wgst")),
            visibility_m=visibility_m,
            temperature_c=_number(latest.get("temp")),
            condition=condition,
            recent=recent,
            outlook=[],  # METAR is an observation, not a forecast
            alerts=[],
            alerts_supported=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def parse_weather(wx_string: str | None) -> tuple[Severity, str]:
    """Map a METAR weather group onto a severity band and a readable label.

    ``-RA`` light, ``RA`` moderate, ``+RA`` heavy — the intensity prefix is the
    observer's own judgement, which is exactly the classification the rules
    engine wants. Thunderstorms and hail escalate beyond the prefix.
    """
    if not wx_string or not wx_string.strip():
        return Severity.NONE, "No significant weather"

    tokens = wx_string.split()
    severity = Severity.NONE
    labels: list[str] = []
    thunderstorm = False
    hail = False

    for token in tokens:
        prefix = Severity.MEDIUM  # no sign means moderate in METAR
        body = token
        if body.startswith("-"):
            prefix, body = Severity.LOW, body[1:]
        elif body.startswith("+"):
            prefix, body = Severity.HIGH, body[1:]
        elif body.startswith("VC"):  # in the vicinity, not at the station
            prefix, body = Severity.LOW, body[2:]

        if "TS" in body:
            thunderstorm = True
        if "GR" in body or "GS" in body:
            hail = True

        # A group is two-letter codes concatenated, e.g. TSRA, SHRA, FZDZ.
        codes = [body[i : i + 2] for i in range(0, len(body) - len(body) % 2, 2)]
        if any(code in PRECIPITATION_CODES for code in codes):
            severity = max(severity, prefix)

        labels.append(_describe(body, codes))

    if thunderstorm:
        severity = max(severity, Severity.MEDIUM)
    if hail:
        severity = max(severity, Severity.HIGH)
    if thunderstorm and severity is Severity.HIGH:
        severity = Severity.SEVERE

    description = ", ".join(label for label in labels if label) or "No significant weather"
    return severity, description[:1].upper() + description[1:]


def _describe(body: str, codes: list[str]) -> str:
    parts: list[str] = []
    if "TS" in body:
        parts.append("thunderstorm")
    if "SH" in body:
        parts.append("showers")
    if "FZ" in body:
        parts.append("freezing")
    parts += [CODE_LABELS[code] for code in codes if code in CODE_LABELS and code != "TS"]
    return " ".join(dict.fromkeys(parts))


def parse_visibility(value) -> float | None:
    """AWC reports statute miles, sometimes as ``"6+"``. Returns metres."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip().rstrip("+")
        try:
            miles = float(cleaned)
        except ValueError:
            return None
    else:
        try:
            miles = float(value)
        except (TypeError, ValueError):
            return None
    return round(miles * STATUTE_MILE_M)


def _knots_to_kmh(value) -> float | None:
    knots = _number(value)
    return round(knots * KNOTS_TO_KMH, 1) if knots is not None else None


def _number(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _station_label(report: dict, icao: str) -> str:
    name = report.get("name")
    return f"{icao} ({name})" if name else icao


def _observed_at(report: dict) -> datetime | None:
    epoch = report.get("obsTime")
    if epoch is not None:
        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    raw = report.get("reportTime")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

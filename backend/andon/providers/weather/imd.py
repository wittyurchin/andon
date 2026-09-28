"""IMD (India Meteorological Department) automatic weather station adapter.

Verified against documentation/imd/api_reference.html and a live call
(2026-09-27) — see docs/provider-verification.md:

* endpoint ``{base}/aws_data?sid=<state id>`` (or ``?id=<station>``);
* documented fields ``ID``, ``STATION``, ``CURR_TEMP``, ``RH``, ``WIND_SPEED``,
  ``MSLP``, ``WEATHER_CODE``, ``Latitude``, ``Longitude``;
* every endpoint answers ``401 {"error":"API key missing"}`` without a key.

Not documented, and therefore configuration rather than code:

* **how the key is sent** — header or query parameter, and its name;
* the state-id table for ``sid``;
* units of ``WIND_SPEED`` / ``CURR_TEMP`` and the ``WEATHER_CODE`` code table;
* an observation timestamp — none is listed among the fields.

So: values without documented units are stored verbatim under ``reported`` and
never classified; ``observed_at`` is marked as receipt time, not source time;
and no rain band is derived from an undocumented weather code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ...domain.enums import HealthStatus
from ...domain.signals import FetchContext, GeoPoint, SourceRef, WeatherSnapshot
from ...geo import haversine_km
from ..base import WeatherProvider
from ..errors import ProviderSchemaError

DOCUMENTED_FIELDS = ("ID", "STATION", "CURR_TEMP", "RH", "WIND_SPEED", "MSLP", "WEATHER_CODE")


class ImdWeatherProvider(WeatherProvider):
    uses_key = True

    def __init__(
        self,
        api_key: str | None,
        auth_header: str | None,
        auth_query_param: str | None,
        state_id: str | None,
        base_url: str = "https://api.imd.gov.in/api/v1",
        timeout_s: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._auth_header = auth_header
        self._auth_param = auth_query_param
        self._state_id = state_id
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="imd-aws",
            name="IMD automatic weather stations",
            kind="weather",
            mode="live",
            attribution="India Meteorological Department",
            docs_url="https://api.imd.gov.in/public/api_reference.html",
        )

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        if not self._api_key:
            return HealthStatus.MISCONFIGURED, {
                "reason": "IMD requires an API key (live call: 401 'API key missing'); set ANDON_IMD_API_KEY",
                "requires": "credentials",
            }
        if not (self._auth_header or self._auth_param):
            return HealthStatus.MISCONFIGURED, {
                "reason": (
                    "IMD does not document how the key is sent. Set ANDON_IMD_AUTH_HEADER or "
                    "ANDON_IMD_AUTH_QUERY_PARAM to the name IMD issues with the key."
                ),
                "requires": "verification",
            }
        if not self._state_id:
            return HealthStatus.MISCONFIGURED, {
                "reason": "Set ANDON_IMD_AWS_STATE_ID for aws_data?sid= (the id table is not published)",
                "requires": "configuration",
            }
        return None

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot:
        params: dict[str, str] = {"sid": str(self._state_id)}
        headers: dict[str, str] = {}
        if self._auth_header:
            headers[self._auth_header] = str(self._api_key)
        else:
            params[str(self._auth_param)] = str(self._api_key)

        response = await self._client.get(f"{self._base_url}/aws_data", params=params, headers=headers)
        response.raise_for_status()
        received_at = datetime.now(timezone.utc)
        return parse_aws(response.json(), point, received_at)

    async def aclose(self) -> None:
        await self._client.aclose()


def parse_aws(payload: Any, point: GeoPoint, received_at: datetime) -> WeatherSnapshot:
    records = _records(payload)
    located = []
    for record in records:
        lat, lon = _float(record.get("Latitude")), _float(record.get("Longitude"))
        if lat is None or lon is None:
            continue
        located.append((haversine_km(point, GeoPoint(lat=lat, lon=lon)), GeoPoint(lat=lat, lon=lon), record))
    if not located:
        raise ProviderSchemaError("no aws_data record carried documented Latitude/Longitude")

    distance_km, location, record = min(located, key=lambda item: item[0])
    station_id = str(record.get("ID")) if record.get("ID") is not None else None
    station_name = str(record.get("STATION")) if record.get("STATION") is not None else None

    return WeatherSnapshot(
        observed_at=received_at,
        observed_at_basis="received",  # no observation time is documented
        precipitation_mm_h=None,
        rain_intensity=None,  # WEATHER_CODE's table is not documented
        station_id=station_id,
        station_name=f"{station_id} ({station_name})" if station_id and station_name else station_name,
        station_location=location,
        station_distance_km=round(distance_km, 1),
        # Verbatim, units unknown: never converted, never classified.
        reported={field: record.get(field) for field in DOCUMENTED_FIELDS if field in record},
        alerts_supported=False,
    )


def _records(payload: Any) -> list[dict]:
    """Find the station records without assuming the undocumented container."""
    if isinstance(payload, list) and all(isinstance(r, dict) for r in payload):
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
                return value
    raise ProviderSchemaError("unrecognised aws_data response container")


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

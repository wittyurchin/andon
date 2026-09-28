"""Environment-based configuration.

Every setting is overridable with an ``ANDON_``-prefixed environment variable,
e.g. ``ANDON_LLM_MODEL=claude-sonnet-5``.

Provider lists are comma-separated strings rather than list-typed fields,
because pydantic-settings JSON-decodes list fields before validators run and
the natural ``ANDON_WEATHER_PROVIDERS=open_meteo,awc_metar`` would fail.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MockScenario = Literal["calm", "deteriorating", "storm", "clearing", "demo"]
AppMode = Literal["auto", "mock"]

WEATHER_PROVIDERS = frozenset({"auto", "open_meteo", "awc_metar", "imd", "ksndmc", "mock", "mock_station"})
TRAFFIC_PROVIDERS = frozenset({"auto", "tomtom", "mappls", "mock", "mock_outage"})
INCIDENT_PROVIDERS = frozenset({"auto", "tomtom", "mappls", "btp", "bmc", "mock"})
RADAR_PROVIDERS = frozenset({"auto", "rainviewer", "mock", "none"})

# Engineering defaults, not provider guarantees: how long each class of
# evidence stays current before it is marked stale. Override per class with
# ANDON_FRESHNESS_POLICIES='{"traffic": 240}' and per source with
# ANDON_SOURCE_FRESHNESS_OVERRIDES='{"awc-metar": 3600}'.
DEFAULT_FRESHNESS_POLICIES: dict[str, int] = {
    "weather_station": 900,
    "weather_model": 1800,
    "radar": 600,
    "traffic": 300,
    "incident": 600,
}
DEFAULT_SOURCE_FRESHNESS_OVERRIDES: dict[str, int] = {
    # Indian METAR stations were observed reporting half-hourly (VOBG,
    # 2026-09-27), so a 15-minute policy would mark every other report stale.
    "awc-metar": 2700,
}


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _dedupe(names: list[str]) -> list[str]:
    out: list[str] = []
    for name in names:
        if name not in out:
            out.append(name)
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANDON_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- app -------------------------------------------------------------
    app_name: str = "Restaurant Situation Awareness"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # Where the built UI lives. Defaults to the repo layout; set explicitly when
    # the package is installed somewhere other than alongside ``frontend/``.
    frontend_dist: str | None = None

    # auto = real sources where free or credentialed, mocks elsewhere.
    # mock = everything simulated (the "demo" scenario), no network at all.
    mode: AppMode = "auto"

    # --- providers -------------------------------------------------------
    weather_providers: str = "auto"
    traffic_providers: str = "auto"
    incident_providers: str = "auto"
    radar_providers: str = "auto"
    # Superseded single-provider settings, still honoured so an existing
    # environment keeps working instead of being silently ignored.
    weather_provider: str | None = None
    traffic_provider: str | None = None
    incident_provider: str | None = None

    tomtom_api_key: str | None = None
    open_meteo_api_key: str | None = None

    # IMD requires a key, but its documentation does not say how the key is
    # sent (docs/provider-verification.md). Both must be configured; we do
    # not guess a header name.
    imd_api_key: str | None = None
    imd_auth_header: str | None = None
    imd_auth_query_param: str | None = None
    imd_base_url: str = "https://api.imd.gov.in/api/v1"
    # State id for the documented ``aws_data?sid=`` query. The id table is
    # not published, so it is configuration too.
    imd_aws_state_id: str | None = None

    # Mappls static key (current auth, sent as the documented `access_token`
    # query parameter). Only corridor routing ETAs are documented publicly.
    mappls_access_token: str | None = None
    mappls_compare_optimal: bool = True

    # RainViewer's free API is for personal and educational use only. It is
    # never called unless this is explicitly acknowledged.
    rainviewer_terms_acknowledged: bool = False

    metar_search_radius_km: float = 120.0

    # Radius around the restaurant we treat as "local".
    local_radius_m: int = 900
    incident_radius_km: float = 5.0

    provider_timeout_s: float = 8.0
    # External responses are cached so that a 1-minute auto-refresh does not
    # translate into a 1-minute external API call rate.
    weather_cache_ttl_s: int = 240
    traffic_cache_ttl_s: int = 120
    incident_cache_ttl_s: int = 180
    radar_cache_ttl_s: int = 300

    # --- restaurant access graph ------------------------------------------
    access_graph_source: Literal["auto", "osm", "configured", "mock"] = "auto"
    access_graph_radius_m: int = 700
    access_max_approaches: int = 6
    access_graph_max_age_days: int = 30
    # Public instance by default; comma-separated to allow a fallback.
    overpass_endpoints: str = "https://overpass-api.de/api/interpreter"
    # Operator-configured approach points, used when no road graph can be
    # derived: JSON list of {"label": ..., "lat": ..., "lon": ...}. These are
    # labelled as configured approximations wherever they appear.
    access_points: str | None = None

    # --- persistence -------------------------------------------------------
    database_path: str = "data/andon.sqlite3"
    retention_days: int = 14

    # --- freshness -----------------------------------------------------------
    freshness_policies: str | None = None
    source_freshness_overrides: str | None = None
    # Legacy grades for the situation engine's display.
    freshness_fresh_s: int = 300
    freshness_recent_s: int = 900
    freshness_aging_s: int = 2700

    # --- change detection ----------------------------------------------------
    change_traffic_index_delta: float = 0.15
    change_travel_time_pct: float = 25.0

    # --- polling ---------------------------------------------------------------
    # Off by default: an unattended dashboard should not keep calling paid APIs.
    background_polling: bool = False
    poll_tick_s: int = 15

    # --- LLM -------------------------------------------------------------
    llm_enabled: bool = True
    llm_model: str = "claude-opus-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    llm_max_tokens: int = 16000
    llm_timeout_s: float = 60.0
    # Cost control: skip the LLM when the normalized situation is unchanged,
    # but never serve a report older than this.
    llm_max_report_age_s: int = 1800
    anthropic_api_key: str | None = None

    # --- history ---------------------------------------------------------
    history_max_snapshots: int = 288
    # Deprecated: situation history now lives in the SQLite database.
    history_file: str | None = None

    # --- mock providers --------------------------------------------------
    mock_scenario: MockScenario = "deteriorating"
    mock_ramp_minutes: float = 20.0

    # -- validation -------------------------------------------------------

    @field_validator("weather_providers", "weather_provider")
    @classmethod
    def _check_weather(cls, value: str | None) -> str | None:
        return _validate(value, WEATHER_PROVIDERS, "weather")

    @field_validator("traffic_providers", "traffic_provider")
    @classmethod
    def _check_traffic(cls, value: str | None) -> str | None:
        return _validate(value, TRAFFIC_PROVIDERS, "traffic")

    @field_validator("incident_providers", "incident_provider")
    @classmethod
    def _check_incidents(cls, value: str | None) -> str | None:
        return _validate(value, INCIDENT_PROVIDERS, "incident")

    @field_validator("radar_providers")
    @classmethod
    def _check_radar(cls, value: str | None) -> str | None:
        return _validate(value, RADAR_PROVIDERS, "radar")

    # -- resolution -------------------------------------------------------

    @property
    def effective_scenario(self) -> MockScenario:
        return "demo" if self.mode == "mock" else self.mock_scenario

    # A configured key always beats a mock, in either mode: the provider runs
    # and, if the key is bad, fails visibly instead of being papered over.
    # Explicit provider lists are honoured as written.

    @property
    def resolved_weather_providers(self) -> list[str]:
        configured = _legacy(self.weather_providers, self.weather_provider)
        if configured == ["auto"]:
            if self.mode == "mock":
                return [
                    "open_meteo" if self.open_meteo_api_key else "mock",
                    "imd" if self.imd_api_key else "mock_station",
                ]
            # Model + station, plus the India sources whose adapters exist but
            # are not usable yet — they appear in source health with the reason.
            return ["open_meteo", "awc_metar", "imd", "ksndmc"]
        return _dedupe([n for n in configured if n != "auto"])

    @property
    def resolved_traffic_providers(self) -> list[str]:
        configured = _legacy(self.traffic_providers, self.traffic_provider)
        if configured == ["auto"]:
            if self.mode == "mock":
                keyed = [n for n, key in (("tomtom", self.tomtom_api_key),
                                          ("mappls", self.mappls_access_token)) if key]
                return keyed or ["mock", "mock_outage"]
            return ["tomtom" if self.tomtom_api_key else "mock", "mappls"]
        return _dedupe([n for n in configured if n != "auto"])

    @property
    def resolved_incident_providers(self) -> list[str]:
        configured = _legacy(self.incident_providers, self.incident_provider)
        if configured == ["auto"]:
            if self.mode == "mock":
                return ["tomtom" if self.tomtom_api_key else "mock"]
            return ["tomtom" if self.tomtom_api_key else "mock", "mappls"]
        return _dedupe([n for n in configured if n != "auto"])

    @property
    def resolved_radar_providers(self) -> list[str]:
        configured = _split(self.radar_providers) or ["auto"]
        if configured == ["auto"]:
            return ["mock"] if self.mode == "mock" else ["rainviewer"]
        return _dedupe([n for n in configured if n not in ("auto", "none")])

    @property
    def needs_real_roads(self) -> bool:
        """Live road-bound sources must sample real roads, never simulated ones."""
        live = {"tomtom", "mappls"}
        return bool(live & {*self.resolved_traffic_providers, *self.resolved_incident_providers})

    @property
    def freshness_policy(self) -> dict[str, int]:
        return {**DEFAULT_FRESHNESS_POLICIES, **_json_dict(self.freshness_policies)}

    @property
    def source_freshness(self) -> dict[str, int]:
        return {**DEFAULT_SOURCE_FRESHNESS_OVERRIDES, **_json_dict(self.source_freshness_overrides)}

    @property
    def configured_access_points(self) -> list[dict]:
        if not self.access_points:
            return []
        points = json.loads(self.access_points)
        if not isinstance(points, list):
            raise ValueError("ANDON_ACCESS_POINTS must be a JSON list")
        return points


def _validate(value: str | None, known: frozenset[str], what: str) -> str | None:
    """Fail at startup on a typo rather than silently falling back."""
    if value is None:
        return value
    unknown = [n for n in _split(value) if n not in known]
    if unknown:
        raise ValueError(f"unknown {what} provider(s) {unknown}; choose from {sorted(known)}")
    return value


def _legacy(plural: str, singular: str | None) -> list[str]:
    configured = _split(plural) or ["auto"]
    if configured == ["auto"] and singular and singular != "auto":
        return [singular]
    return configured


def _json_dict(raw: str | None) -> dict[str, int]:
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return {str(k): int(v) for k, v in data.items()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

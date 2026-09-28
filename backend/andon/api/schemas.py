"""Request/response models that exist only at the API boundary."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.enums import Trend
from ..evidence.models import IncidentEvidence, Observation, SourceHealthRecord
from ..domain.signals import SourceRef
from ..domain.situation import SituationSnapshot


class HistoryResponse(BaseModel):
    location: str
    window_minutes: int
    snapshots: list[SituationSnapshot]
    note: str | None = None
    direction: Trend = Trend.UNKNOWN


class LlmStatus(BaseModel):
    available: bool
    status: str
    model: str | None = None
    effort: str | None = None


class InactiveSource(BaseModel):
    source: SourceRef
    status: str
    reason: str | None = None
    requires: str | None = None


class ProviderStatus(BaseModel):
    # Every signal can have several providers running side by side.
    weather: list[SourceRef]
    radar: list[SourceRef] = []
    traffic: list[SourceRef]
    incidents: list[SourceRef]
    # Adapters that exist but are not called, with why.
    inactive: list[InactiveSource] = []


class StatusResponse(BaseModel):
    providers: ProviderStatus
    llm: LlmStatus
    cached_reports: int
    mock_scenario: str
    mode: str
    database_path: str


class RestaurantCreate(BaseModel):
    restaurant_name: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    refresh_interval_seconds: int = Field(default=300, ge=30, le=86400)


class HistoryBundle(BaseModel):
    restaurant_id: str
    window_minutes: int
    observations: list[Observation]
    situation: list[SituationSnapshot]


class SourceDetail(BaseModel):
    """Everything known about one source for one restaurant."""

    restaurant_id: str
    source: SourceRef | None
    health: SourceHealthRecord | None
    observations: list[Observation]
    forecasts: list[Observation]
    incidents: list[IncidentEvidence]
    history: list[Observation]
    history_minutes: int
    verification_doc: str = "docs/provider-verification.md"

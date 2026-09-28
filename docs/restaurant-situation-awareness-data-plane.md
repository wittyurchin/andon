# Restaurant Situation Awareness System — Data Plane Specification

## 1. Purpose

Build the evidence/data layer for a real-time Restaurant Situation Awareness System.

The system answers:

> What is happening around this restaurant right now, how is it changing, and what evidence should an operational reasoning engine use to determine the effect on the kitchen and riders?

This document deliberately focuses on **building rich, trustworthy data sources first**. It does not define the final LLM reasoning strategy except where the data contract must support it.

---

## 2. Non-negotiable data principles

1. **Never fabricate missing data.**
   - Provider failure means `unavailable`, not `normal`.
   - Missing data must reduce evidence completeness/confidence.

2. **Every observation is provenance-rich.**
   At minimum:
   - `value`
   - `source`
   - `observed_at`
   - `received_at`
   - `freshness_seconds`
   - `confidence`
   - `distance_m` where spatial relevance applies
   - `source_record_id` where available

3. **Keep observation, forecast and inference separate.**
   - Observation = provider measured/reported it.
   - Forecast = provider/model predicts it.
   - Inference = our system concludes it.

4. **Provider capabilities must be verified, not assumed.**
   Pricing, quotas, commercial rights, API availability, geographic coverage and licensing must be treated as configuration/verification items and never hard-coded as facts.

5. **The normalized evidence layer is vendor-neutral.**
   The UI and reasoning engine consume normalized evidence, not vendor payloads.

6. **Source disagreement is data.**
   Do not silently select a winner. Preserve conflicting observations and expose disagreement to downstream logic.

7. **Historical state matters.**
   Persist observations and incidents so we can reason about change, not just current conditions.

---

## 3. Target architecture

```text
                         EXTERNAL / FIRST-PARTY SOURCES
                                      |
        +-----------------------------+-----------------------------+
        |             |               |               |             |
     Weather       Traffic         Incidents       Flood       Local Reports
        |             |               |               |             |
        +-----------------------------+-----------------------------+
                                      |
                              Provider Adapters
                                      |
                              Raw Evidence Layer
                                      |
                           Normalization / Validation
                                      |
                    Restaurant Access Graph / Spatial Model
                                      |
                        Historical Evidence Persistence
                                      |
                        Evidence + Source Health Registry
                                      |
                              Situation Engine
                                      |
                               LLM Reasoning
                                      |
                               Situation Report
```

The current application can remain a **single FastAPI process**. No microservices are required for this phase.

Use SQLite for persistence initially.

---

## 4. Source portfolio

### 4.1 Weather

#### P0 — IMD

Integrate the official India Meteorological Department API platform as a primary India weather evidence source.

Candidate evidence categories include:

- station/current observations
- rainfall
- forecasts/nowcasts
- warnings
- radar/specialized weather information where API access permits
- lightning/severe-weather information where available through the authorized interface

Treat API access/authentication/entitlements as deploy-time configuration.

Official reference:
https://api.imd.gov.in/public/index.php

#### P0 — KSNDMC

For Karnataka/Bengaluru, integrate Karnataka State Natural Disaster Monitoring Centre where an authorized and technically stable access path exists.

Potential evidence includes:

- near-real-time rainfall
- rainfall intensity
- temperature
- humidity
- wind
- weather stations
- rain gauges
- Bengaluru storm-drain water-level sensors
- flood-vulnerable areas

KSNDMC states that its weather monitoring network collects data at 15-minute intervals and that Bengaluru has telemetric water-level sensors and flood-vulnerable-area mapping.

Treat commercial/operational usage rights and actual API/feed availability as verification items. Never assume that a public webpage implies permission for unrestricted commercial API consumption.

References:
https://ksndmc.org/en/Activities/Weather
https://ksndmc.org/en/Activities/Hydrology

#### P0 — Radar evidence

Add a radar adapter using an authorized radar-data source.

Radar is important because a point station can be dry while precipitation is close by.

The adapter should produce spatial precipitation observations around the restaurant, not a single city-level rain flag.

Do not make a third-party radar provider's discontinued or optional nowcast capability a hard dependency. Verify current product/API behavior before implementation.

#### Keep — Open-Meteo

Retain Open-Meteo as a **forecast/model** source.

Do not call it an observation source.

Its role is short-term forecast context and outlook, not proof that it is currently raining at the restaurant.

---

### 4.2 Traffic

#### P0 — Mappls

Integrate Mappls/MapmyIndia traffic capabilities as the India-first traffic source.

The integration should seek:

- live traffic flow/speed
- congestion
- traffic events/incidents
- closures/roadworks/hazards where available
- historical traffic information where commercially available

Mappls currently advertises live traffic, traffic-event data, historical traffic information, route data with traffic delays, and hyperlocal safety/incident categories including water logging.

Reference:
https://about.mappls.com/traffic/
https://about.mappls.com/hyperlocal-maps/

Do not hard-code pricing or quota assumptions.

#### P0 — TomTom

Integrate TomTom as an independent traffic/incident source.

Target:

- traffic flow
- current speed vs free-flow speed
- travel-time/delay information
- incidents
- closures and road disruptions where available

Use the current recommended TomTom Traffic APIs after verifying documentation and account access.

Treat source disagreement as useful evidence.

---

### 4.3 Local/authority incident sources

Build an adapter framework rather than one giant "India incidents" provider.

Start with city-specific adapters as evidence permits.

#### Bengaluru

Investigate and integrate machine-readable or officially supported Bengaluru Traffic Police traffic/event information where access permits.

Useful categories:

- accidents
- closures/diversions
- congestion events
- road restrictions
- major events

Do not scrape an interactive map unless its current terms and technical access make that appropriate.

#### Mumbai

Investigate BMC Disaster Management data and official feeds/pages that expose:

- waterlogging/flooding
- road obstruction
- traffic congestion
- heavy rain/disaster incidents
- other local emergency events

BMC's published disaster-management material explicitly distinguishes categories including traffic congestion, road-related incidents and water logging/flooding.

Reference:
https://dm.mcgm.gov.in/

Again, the integration must be based on an actual supported interface/feed, not an assumption that a public webpage is an API.

---

## 5. Data sources we should build ourselves

These are as important as third-party integrations.

### 5.1 Restaurant Access Graph

Build a local geospatial model that answers:

> Which roads and approach corridors are operationally relevant to this restaurant?

Do not use five arbitrary lat/lon points forever.

Build:

```text
restaurant
  -> nearby road network
  -> relevant access corridors
  -> road segments
  -> segment metadata
  -> provider traffic observations
```

The first implementation can use OpenStreetMap/Overpass or another appropriately licensed road-network source.

The graph should support:

- restaurant entrance/road association
- north/east/south/west or route-based approach grouping
- segment geometry
- distance from restaurant
- road hierarchy/class
- directionality where available
- provider segment correlation identifiers where possible

This is a derived dataset and must be explicitly labelled as such.

---

### 5.2 Historical Observation Archive

Persist every normalized observation needed to understand change.

Minimum retention model:

```text
observation
source
subject/location
observed_at
received_at
value
confidence
freshness
```

This allows the system to answer:

- Is traffic worsening?
- Is rainfall intensifying?
- Is the road recovering?
- How unusual is current traffic for this restaurant/time-of-day?
- Did an incident remain active for 30 minutes?

Start with SQLite.

---

### 5.3 Incident Evidence Archive

Every incident should become a time-varying evidence record.

Example lifecycle:

```text
12:05  incident first observed
12:15  still active
12:30  still active
12:45  no longer reported
```

Never simply overwrite the latest incident payload.

Store:

- source
- source incident id
- type
- location/geometry
- first_seen
- last_seen
- source timestamp
- lifecycle/status
- confidence
- raw/normalized evidence reference

---

### 5.4 Source Health Registry

Each provider/feed needs an explicit health state.

Examples:

```text
healthy
stale
degraded
unavailable
misconfigured
unauthorized
```

The registry should record:

- last successful fetch
- last failure
- consecutive failures
- latency
- response age
- authentication status when known
- coverage/entitlement status when known

Provider health must never silently alter an observation to "normal".

---

### 5.5 Human/rider reports — later P1

Create the schema now, but do not make it a mandatory source in the first release.

A rider or restaurant staff member could report:

- waterlogging
- road blockage
- unusually heavy traffic
- heavy rain at the restaurant
- road clear

These are observations with provenance, not truth overrides.

A single human report should never automatically become a confirmed incident.

---

### 5.6 Restaurant-local sensing — future

The architecture should leave room for first-party evidence from:

- CCTV/vision
- rain sensors
- entrance/water sensors
- rider queue observations

These are future sources and should not be fabricated or simulated in production.

---

## 6. Canonical evidence model

Every provider adapter must normalize into common types.

### Observation

```python
class Observation(BaseModel):
    id: str
    source_id: str
    source_type: str
    category: str
    subject_id: str | None
    observed_at: datetime
    received_at: datetime
    location: GeoPoint | None
    distance_m: float | None
    value: dict
    confidence: float
    freshness_seconds: int
    observation_kind: Literal["observation", "forecast"]
    source_record_id: str | None
    raw_reference: str | None
```

### Incident

```python
class IncidentEvidence(BaseModel):
    id: str
    source_id: str
    source_record_id: str | None
    incident_type: str
    status: str
    location: GeoPoint
    geometry: dict | None
    first_seen: datetime
    last_seen: datetime
    observed_at: datetime
    confidence: float
    attributes: dict
```

### Source health

```python
class SourceHealth(BaseModel):
    source_id: str
    status: Literal["healthy", "stale", "degraded", "unavailable", "misconfigured", "unauthorized"]
    checked_at: datetime
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    latency_ms: int | None
    details: dict
```

---

## 7. Spatial relevance

Every spatial source must be evaluated relative to the restaurant.

For each piece of evidence calculate where appropriate:

- point distance from restaurant
- distance from relevant approach segment
- whether an incident intersects/affects an approach
- bearing/direction relative to restaurant
- spatial freshness/validity

Never turn a city-wide observation into a restaurant-local observation without an explicit spatial rule.

Example:

```text
City rainfall = heavy
```

must not automatically become:

```text
Restaurant rainfall = heavy
```

unless the source observation actually covers that location or the system explicitly labels the result as a forecast/derived spatial inference.

---

## 8. Freshness

Each source gets a source-specific freshness policy.

Example configuration only:

```yaml
weather_station:
  stale_after_seconds: 900

traffic:
  stale_after_seconds: 300

incident:
  stale_after_seconds: 600

forecast:
  stale_after_seconds: 1800
```

These are **engineering defaults**, not claims about provider guarantees. They should be configurable per source after observing actual feed behavior.

A stale observation remains stored but is not silently treated as current.

---

## 9. Conflict handling

Never merge conflicting providers by silently choosing one.

Example:

```text
Open-Meteo       -> moderate precipitation forecast
Weather station  -> no current rain
Radar            -> light precipitation nearby
```

Normalized state should preserve all three.

Downstream classification may produce:

```text
precipitation.current_status = uncertain
agreement = partial
confidence = reduced
```

The evidence layer must not lose the original observations.

---

## 10. Change detection

Persist enough history to detect material changes.

A change event can be generated when:

- a severity band crosses a threshold
- an incident appears/disappears
- traffic delay changes materially
- precipitation intensity changes materially
- source confidence changes materially
- a previously unavailable source becomes healthy
- an important approach changes state

Avoid calling the LLM merely because a timer fired.

---

## 11. Initial implementation scope

Build this first:

### Weather

1. Existing Open-Meteo adapter retained.
2. Existing METAR adapter retained.
3. IMD adapter framework + verified endpoints/configuration placeholders.
4. KSNDMC adapter interface + Bengaluru-specific implementation only where an authorized access route is known.
5. Radar adapter interface.

### Traffic

6. Mappls adapter.
7. TomTom adapter.
8. Replace fixed five traffic points with an Access Graph abstraction.

### Incidents

9. Normalize traffic incidents from Mappls/TomTom.
10. Create authority-incident adapter interface.
11. Implement the first city-specific authority connector only when the source interface is verifiably accessible.

### Persistence

12. SQLite observation store.
13. SQLite incident store.
14. Source health store.
15. Access Graph store.

### UI/API

16. Expose normalized evidence through the existing API.
17. Show provider identity, timestamp, age, location/distance and confidence.
18. Show unavailable/stale status explicitly.
19. Do not expose raw vendor JSON as part of the primary UI.

---

## 12. Definition of Done

The data layer is done for a restaurant when it can:

1. Fetch multiple independent weather signals.
2. Fetch multiple traffic signals.
3. Associate traffic observations with actual approaches/road segments.
4. Normalize incidents from multiple providers.
5. Preserve provider disagreement.
6. Persist observations over time.
7. Detect changes without an LLM.
8. Detect stale/unavailable sources.
9. Show exactly where each fact came from.
10. Refuse to fill missing information with guesses.
11. Produce an evidence bundle that a separate reasoning engine can consume.

The resulting evidence bundle should make it possible to reason about:

> current state + spatial distribution + trend + uncertainty + provenance

without asking the LLM to fetch, interpret or invent raw data.

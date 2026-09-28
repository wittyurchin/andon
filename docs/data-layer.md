# Data layer

How a restaurant's evidence is gathered, classified, stored and exposed.
Provider facts (endpoints, auth, licences) live in
[provider-verification.md](provider-verification.md); this document covers how
the system uses them.

The rule behind every section below: **no fabricated data.** A missing value
stays missing, a reading somewhere else is not a reading here, and a source
that cannot answer is reported as unable to answer — never as "all clear".

```
restaurant (lat, lon)
   │
   ├─ access graph ─── which roads lead to the kitchen (OSM, cached in SQLite)
   │
   ├─ providers ────── every configured source, fetched in parallel
   │                   (failures contained; inactive sources listed, not called)
   │
   ├─ extraction ───── provider snapshots → Observation / IncidentEvidence
   │                   with kind, spatial context, confidence, freshness
   │
   ├─ SQLite ───────── observations, incidents, source health, changes, history
   │
   ├─ change detection  digest of this refresh vs the previous one
   │
   └─ evidence bundle ─ GET /api/restaurants/{id}/evidence
```

Code: `backend/andon/evidence/` (models, store, extract, spatial, freshness,
health, changes, access_graph, service, poller) and `backend/andon/providers/`.

---

## 1. Provider configuration

All settings are environment variables with the `ANDON_` prefix, read from
the process environment or `.env`. See [`.env.example`](../.env.example) for
every setting with comments.

Each signal takes a **comma-separated list**; every listed provider runs, gets
its own source card, and is assessed independently:

| Setting | Values |
|---|---|
| `ANDON_WEATHER_PROVIDERS` | `open_meteo`, `awc_metar`, `imd`, `ksndmc`, `openweathermap`, `weatherapi`, `ndma_sachet`, `mock`, `mock_station` |
| `ANDON_TRAFFIC_PROVIDERS` | `tomtom`, `mappls`, `mock`, `mock_outage` |
| `ANDON_INCIDENT_PROVIDERS` | `tomtom`, `mappls`, `btp`, `bmc`, `mock` |
| `ANDON_RADAR_PROVIDERS` | `rainviewer`, `mock`, `none` |

An unknown name fails at startup rather than silently falling back.

`auto` (the default for each list) resolves by `ANDON_MODE`:

| Signal | `ANDON_MODE=auto` | `ANDON_MODE=mock` |
|---|---|---|
| Weather | `open_meteo, awc_metar, imd, ksndmc, openweathermap, weatherapi, ndma_sachet` | `open_meteo` if `ANDON_OPEN_METEO_API_KEY` is set, else `mock`; `imd` if `ANDON_IMD_API_KEY` is set, else `mock_station`; `openweathermap`/`weatherapi` each appended if their key is set. `ndma_sachet` needs no key and is **not** included in mock mode (it makes real network calls; mock mode promises none) |
| Traffic | `tomtom` if `ANDON_TOMTOM_API_KEY` is set, else `mock`; plus `mappls` | the keyed providers (`tomtom`, `mappls`) if any key is set, else `mock, mock_outage` |
| Incidents | `tomtom` if keyed, else `mock`; plus `mappls` | `tomtom` if keyed, else `mock` |
| Radar | `rainviewer` | `mock` |

**A configured key always replaces the mock**, in either mode. If the key is
then rejected or out of quota, the source fails visibly (below) — it never
falls back to a mock. When a live road-bound source is active, the access
graph is built from real roads even in mock mode, and a cached simulated graph
is rebuilt. Explicit provider lists (anything other than `auto`) are used
exactly as written.

Sources in the `auto` list that cannot run (no key, no feed, licence not
acknowledged) still appear — in source health, with the reason.

Credentials:

| Setting | Source | Notes |
|---|---|---|
| `ANDON_TOMTOM_API_KEY` | TomTom flow + incidents | Sent as the documented `key` query parameter. |
| `ANDON_MAPPLS_ACCESS_TOKEN` | Mappls corridor ETAs | Static console key, documented `access_token` query parameter. The key must have Predictive Routing enabled. |
| `ANDON_OPEN_METEO_API_KEY` | Open-Meteo | Switches to the commercial host. Without it, the free non-commercial API is used. |
| `ANDON_OPENWEATHERMAP_API_KEY` | OpenWeatherMap | Free, no card needed for this endpoint. Its severe-weather alerts are a separate paid product requiring billing details even for the free quota, so they are not fetched. |
| `ANDON_WEATHERAPI_KEY` | WeatherAPI.com | Free, no card needed, commercial use permitted. Its `precip_mm` has no documented time window, so it is stored verbatim rather than treated as a rate. |
| *(none)* | NDMA Sachet | No key. Official India disaster alerts (public domain feed), matched to a restaurant's **state** (not district — see docs/provider-verification.md) via OpenStreetMap reverse geocoding. On by default outside mock mode. |
| `ANDON_IMD_API_KEY` + `ANDON_IMD_AUTH_HEADER` *or* `ANDON_IMD_AUTH_QUERY_PARAM` | IMD | IMD does not document how the key is sent, so the name must be configured; none is guessed. `ANDON_IMD_AWS_STATE_ID` is also configuration (the id table is unpublished). |
| `ANDON_RAINVIEWER_TERMS_ACKNOWLEDGED=true` | RainViewer | Not a credential: an explicit statement that your use is personal or educational, which the free API requires. |

Provider errors are redacted before they are logged or returned: the value of
any `key`, `api_key`, `apikey`, `token`, `access_token` or `subscription-key`
query parameter is replaced with `***`.

Responses are cached per location (`ANDON_*_CACHE_TTL_S`) so a one-minute
dashboard refresh does not become a one-minute external call rate. A cached
response keeps its original observation time.

---

## 2. How source health works

Every refresh updates one `SourceHealthRecord` per (restaurant, source) —
including sources that were deliberately not called
(`evidence/health.py`).

| Status | Meaning | Called? | Counts as a failure? |
|---|---|---|---|
| `healthy` | Answered; data within its freshness policy | yes | — |
| `degraded` | Answered, but part of the request failed (e.g. some corridor probes) | yes | — |
| `stale` | Answered, but the data is older than its freshness policy | yes | — |
| `unavailable` | Call failed: timeout, 5xx, bad payload, quota exceeded | yes | yes |
| `unauthorized` | Credentials rejected (401/403, except where the provider documents 403 as quota) | yes | yes |
| `misconfigured` | A required setting is missing (key, key transport) | no | no |
| `disabled` | No authorised access path exists (no API, licence restriction) | no | no |

A missing key is not an outage, so `misconfigured` and `disabled` never
increment `consecutive_failures`.

**Key failures are loud.** For a source that sends a credential, HTTP 401/403
is classified as `unauthorized` with `details.key_problem = "rejected"`
("API key rejected — invalid, expired, or not enabled for this API"), and HTTP
429 — or Mappls' documented 403 — as `unavailable` with
`key_problem = "limit_reached"` ("API usage limit reached for this key"). Such
failures are logged at ERROR, and the dashboard shows a red alert above the
source cards naming each failing source and its error. The provider's own
reason is kept where it gives one (e.g. Mappls `ASSET_ACCESS_DENIED`). Its data
is simply missing from the assessment; nothing is substituted. A keyless
source's 403 is not blamed on a key.

Each record carries `last_success_at`, `last_failure_at`,
`consecutive_failures`, `latency_ms`, `response_age_seconds`, the error
(redacted) and `details` — `reason` and `requires` for inactive sources,
`stale_after_seconds`, and for incident feeds `covered_categories` (what the
feed *can* report, so a category it doesn't cover is shown as "not covered",
not "none reported").

Health never alters evidence. A stale source's observations stay stored and
visible, marked stale.

Each record also carries `freshness_grade` — `response_age_seconds` graded
against the source's own freshness policy on the same fresh/recent/aging/stale
scale used for `SignalAssessment.freshness` (`unknown` when the source was
never called). Computed once in `evidence/health.py::update_health` via
`FreshnessPolicy.grade_for`, not recomputed by the UI, so there is one
staleness scale, not two. The Sources panel's weather cards render it with a
small `FreshnessMeter` (`frontend/src/components/FreshnessMeter.tsx`).

Weather cards in the Sources panel also carry provider chrome that is *not*
evidence — a real/model/prediction/simulated badge (derived from the card's
lead observation's `kind`/`valid_at`), a free/paid badge, and coverage icons
for what each source can report at all (precipitation, wind, gusts,
visibility, temperature, forecast, severe alerts). This is a static lookup in
`frontend/src/lib/sourceMeta.ts`, grounded in the adapter files and this
document — update it when a weather provider is added or its capabilities
change. Each observation's `value` also carries a `location_label` (the
station's friendly name, or "Model grid cell") alongside the distance/bearing
already in `spatial`, so a card can show where a reading came from without
opening the drawer.

**Freshness policies** (`evidence/freshness.py`) are engineering defaults, not
provider guarantees:

| Class | Stale after |
|---|---|
| `weather_station` | 900 s |
| `weather_model` (all forecasts) | 1800 s |
| `radar` | 600 s |
| `traffic` | 300 s |
| `incident` | 600 s |

Per-source override: `awc-metar` 2700 s, because Indian METAR stations report
half-hourly. Change either with `ANDON_FRESHNESS_POLICIES` /
`ANDON_SOURCE_FRESHNESS_OVERRIDES` (JSON objects).

Endpoints: `GET /api/restaurants/{id}/sources` (all) and
`GET /api/restaurants/{id}/sources/{source_id}` (one source with its current
readings, forecasts, incidents and history). The UI shows one card per source;
clicking a card opens that detail.

---

## 3. Observation vs forecast vs inference

| Kind | Stored? | What it is |
|---|---|---|
| `observation` | yes | Measured or reported by the source: a station report, a radar echo, a traffic speed, an incident record. |
| `forecast` | yes | A model's value. **All** model output is forecast evidence, including its "current" and "past" values — a model's value for now is still a model's opinion. Open-Meteo is always `forecast`. |
| inference | **never** | The engine's own deduction (e.g. waterlogging *risk* from sustained rain). Produced by the situation layer, tagged `inferred`, never written to the evidence store. |

The database enforces the first two with a `CHECK (kind IN ('observation',
'forecast'))` constraint.

Classification happens in one place, `evidence/extract.py`:

- A source with a station → `observation`; without → `forecast`.
- Radar, traffic and incident feeds → `observation`.
- Values whose units the provider does not document (IMD's `WIND_SPEED`,
  `WEATHER_CODE`) are stored verbatim with `units_documented: false` and never
  banded.
- Nothing is filled in. No gust → `None` (a sustained wind is never copied into
  the gust field). No free-flow speed → no congestion index. No record id →
  `None`.
- Mappls' `optimal` ETA is stored as `reference_travel_time_s` with
  `reference_kind="mappls_optimal_eta"`, because it is not free-flow and must
  not be read as one.

`observed_at_basis` records where each timestamp came from — `source` (the
provider's own observation time) or `received` (the provider gave none, so it
is our receipt time). The UI marks receipt-time readings.

**Confidence, relevance and freshness are separate fields** and are never
multiplied into one number:

- *confidence* — how much the measurement itself is trusted. The provider's
  own figure when it gives one (TomTom flow, TomTom incident probability);
  otherwise an engineering default per class (station 0.9, traffic 0.8, radar
  0.75, model 0.6).
- *relevance* — how much it says about this restaurant (next section).
- *freshness* — its age against its policy.

---

## 4. How spatial relevance is calculated

Every observation and incident carries a `SpatialContext`
(`evidence/spatial.py`): distance and bearing from the restaurant,
applicability band, relevance 0–1, nearest access-graph segment, the corridors
it sits on, and a plain-language `basis`.

**Applicability bands** (by distance):

| Band | Distance |
|---|---|
| `at_site` | ≤ 150 m |
| `local` | ≤ 1 km |
| `nearby` | ≤ 5 km |
| `regional` | ≤ 30 km |
| `distant` | beyond |

**Area phenomena** (rain, visibility, radar) decay smoothly with distance,
linearly interpolated between these knots:

| Distance | 0–1 km | 5 km | 10 km | 30 km | 60 km | beyond |
|---|---|---|---|---|---|---|
| Relevance | 1.0 | 0.8 | 0.6 | 0.3 | 0.1 | 0.05 |

A gridded reading whose cell contains the restaurant is `at_site` (cell ≤ 1.5
km) or `local` (coarser). A model's grid-cell centre is used as its location,
so "model estimate for a grid cell 2 km away" is what the UI says.

**Road-bound phenomena** (traffic, incidents) depend on whether they are **on
an access corridor**, or sampled by the provider for that corridor:

- **A point** (e.g. an accident reported as a point) is on a corridor within
  20 m of it.
- **A line** (a closure or a jam, reported with the stretch it covers) is on a
  corridor only if at least 30 m of it runs within 20 m of the corridor *and
  roughly parallel* to it (within 35°). A street crossing at a junction fails
  the parallel test; a parallel street fails the distance test.
- Within 60 m but not on it, the item is **near** the corridor
  (`near_approach_ids`, `near_distance_m`): listed under that road, never
  counted in its severity. Within 20 m but not running along it, it
  **meets** the road (crosses or joins it).

The thresholds were measured on live TomTom incidents around HSR Layout
(2026-09-28): incidents on Outer Ring Road sat 1 to 12 m from the
OpenStreetMap line, while a closure on the parallel 13th Cross Road sat 35 m
away along its whole length. The previous single 40 m radius counted that
closure as being on Outer Ring Road.

| Distance | on a corridor | off corridor |
|---|---|---|
| ≤ 300 m | 1.0 | 0.7 |
| 1 km | 1.0 | 0.5 |
| 2.5 km | 0.85 | 0.3 |
| 5 km | 0.7 | 0.15 |
| 10 km | — | 0.05 |

Evidence tied to a radial or configured approximation (no mapped road) is
multiplied by 0.6.

All thresholds are engineering conventions, stated once in
`evidence/spatial.py`.

### The access graph

Which roads lead to the kitchen (`evidence/access_graph.py`):

1. Fetch drivable roads within `ANDON_ACCESS_GRAPH_RADIUS_M` (700 m) from
   OpenStreetMap via Overpass.
2. The nearest road within 150 m is the **frontage**.
3. Major roads are grouped by name and split by the compass sector they
   extend into — "27th Main Road · north" and "· south" are separate
   corridors. Fragments shorter than 150 m are dropped.
4. Ranked by road class then proximity; the top `ANDON_ACCESS_MAX_APPROACHES`
   (6) are kept.
5. Each corridor gets a **probe** ~250 m out (where flow is sampled) and an
   **entry** at its far end (for corridor travel times).

It is a grouping of mapped roads, not a routing analysis, and is labelled as
such. The graph is built once per restaurant and cached in SQLite for
`ANDON_ACCESS_GRAPH_MAX_AGE_DAYS`. When Overpass is unavailable, the fallback
is `ANDON_ACCESS_POINTS` (operator-configured) and then radial points; both are
flagged `is_approximation` everywhere they appear. OSM data is credited
"© OpenStreetMap contributors, ODbL".

Traffic providers sample each corridor's probe. TomTom returns the segment
nearest the point it is given; the gap between that segment and our corridor
is stored as `match_distance_m`, and a large gap carries a `match_warning`
("it may be a different road") instead of being silently attributed.

---

## 5. How to add a new provider

1. **Verify first.** Record the endpoint, auth transport, field semantics,
   units and terms in `docs/provider-verification.md`, from the provider's own
   documentation (save a copy under `documentation/`). Do not guess any of
   them.
2. **Write the adapter** in `backend/andon/providers/<signal>/<name>.py`,
   subclassing `WeatherProvider`, `RadarProvider`, `TrafficProvider` or
   `IncidentProvider` (`providers/base.py`):
   - `source` → a `SourceRef` (stable `id`, name, kind, `mode="live"`,
     attribution, docs URL, `licence_note` if the terms constrain use).
   - `precheck()` → return `(HealthStatus.MISCONFIGURED, {"reason": ...,
     "requires": "credentials"})` when a setting is missing, `DISABLED` when no
     authorised path exists, or `None` to run.
   - `_fetch(point, context)` → return the signal's snapshot
     (`WeatherSnapshot`, `TrafficSnapshot`, …) from `domain/signals.py`. Use
     `context.approaches` to sample corridors. Raise on failure; the base class
     classifies and redacts the error, times the call and never lets it
     propagate.
   - Leave unknown fields `None`. Set station/grid location, observation time
     and `raw_text` where the provider gives them.
3. **Register it**: add the name to the allowed set in `config.py` and a
   branch in the matching `ProviderRegistry._<signal>()` constructor in
   `providers/registry.py`. Add any credentials as `Settings` fields.
4. **Extraction** needs no change if the provider fills the existing snapshot.
   A new kind of measurement needs a mapping in `evidence/extract.py`.
5. **Test** with a captured payload under `backend/tests/fixtures/` — parsing,
   the precheck, and at least one failure path. Tests never use the network.

A source with no access path yet can still be listed: add a factory to
`providers/disabled.py` with the exact blocker. It shows as `disabled` in
source health and is never called.

---

## 6. Running with mocks

```bash
ANDON_MODE=mock ./run.sh
```

No network and no credentials needed — but any key present in the environment
or `.env` replaces the matching mock (section 1), and that source then goes to
the network. Every source is simulated, marked
`source_type = mock`, and labelled "mock" / "simulated" on every card and
reading in the UI.

`ANDON_MODE=mock` runs the **demo scenario**, a fixed, deliberately mixed
picture driven by one shared clock (`providers/scenario.py`):

- moderate rain at the kitchen, intensifying over the past hour (mock station);
- a mock forecast model that over-reads it as heavy — a real source conflict,
  shown as disagreement;
- traffic worsening on one approach, the others steady;
- waterlogging on a second approach from the start;
- an accident on a third approach appearing ~3 minutes in (a change event);
- a second traffic feed that is always down (`unavailable` in source health);
- simulated roads for the access graph;
- backfilled history, so trends are visible on the first refresh.

History backfill is done **only** for mock providers. Real providers get
history by being polled over time.

Other scenarios (`ANDON_MOCK_SCENARIO=calm | deteriorating | storm |
clearing`) drive only the mock providers configured in `auto` mode — for
example, mock traffic alongside real weather.

---

## 7. How SQLite persistence works

One file, stdlib `sqlite3`, WAL mode, no ORM (`evidence/store.py`). Path:
`ANDON_DATABASE_PATH` (default `data/andon.sqlite3`, relative to the backend's
working directory). Evidence survives restarts.

| Table | Holds |
|---|---|
| `restaurants` | id, name, location, refresh interval, last refresh |
| `observations` | every observation and forecast, with spatial context, value, confidence, freshness policy, source record id, raw reference |
| `incidents` | incident lifecycle per restaurant: `first_seen`, `last_seen`, `status`, `cleared_at` |
| `source_health` | latest health record per (restaurant, source) |
| `access_graphs`, `access_graph_approaches`, `access_graph_segments`, `access_graph_nodes` | the cached access graph |
| `change_events` | detected changes |
| `evidence_state` | the latest state digest per restaurant (the baseline for change detection) |
| `situation_snapshots` | situation-report history |

Behaviour worth knowing:

- **Only normalised evidence is stored.** Raw payloads are not kept;
  `raw_reference` holds a short pointer (a METAR string, a radar frame path).
- **Observation ids are deterministic** (restaurant, source, category,
  subject, kind, observation time, valid time), inserted with `INSERT OR IGNORE`. Re-reading a cached response or an
  unchanged station report does not duplicate history.
- **Restaurant ids** are `r_` + a hash of the rounded location, so
  registering the same place twice is idempotent.
- **Incidents** keep their original `first_seen`. An incident is cleared only
  when the source that reported it answers without it — a failed refresh
  clears nothing.
- **Retention**: rows older than `ANDON_RETENTION_DAYS` (14) are pruned
  periodically during refreshes.

**Change detection** (`evidence/changes.py`): each refresh reduces the bundle to
a digest — severity bands, congestion indices, travel times, incident ids,
source statuses — and compares it with the previous one. Differences become
`ChangeEvent`s: `precipitation_intensified` / `_weakened` (band changes),
`traffic_worsened` / `_improved` (moves beyond
`ANDON_CHANGE_TRAFFIC_INDEX_DELTA` or `ANDON_CHANGE_TRAVEL_TIME_PCT`),
`approach_state_changed`, `incident_appeared` / `_cleared`,
`source_became_available` / `_unavailable`, `source_health_changed`,
`confidence_changed` and `situation_level_changed`. The
first refresh is the baseline and emits nothing; an unchanged state emits
nothing.

**Background polling** (`ANDON_BACKGROUND_POLLING=true`) refreshes each
registered restaurant on its own interval so history accumulates with no
dashboard open. Off by default, because it calls external APIs around the
clock. It never calls the LLM.

### API

| Endpoint | Purpose |
|---|---|
| `POST /api/restaurants` | Register (idempotent per location) |
| `GET /api/restaurants`, `GET /api/restaurants/{id}` | List / get |
| `POST /api/restaurants/{id}/refresh` | Poll providers, store evidence, detect changes. Never calls the LLM. |
| `GET /api/restaurants/{id}/evidence` | The evidence bundle from the store (`?refresh=true` polls first) |
| `GET /api/restaurants/{id}/sources` | Source health for every source |
| `GET /api/restaurants/{id}/sources/{source_id}` | One source in full: health, provenance, readings, forecasts, incidents, history |
| `GET /api/restaurants/{id}/changes` | Change events |
| `GET /api/restaurants/{id}/history` | Stored observations, filterable by category, source, subject, kind |
| `GET /api/restaurants/{id}/incidents` | Incidents, `?status=active\|cleared` |
| `GET /api/restaurants/{id}/access-graph` | The access graph (`?rebuild=true`) |
| `GET /api/restaurants/{id}/situation` | Situation report built on the evidence |

---

## 8. Provider capabilities that still need verification

From [provider-verification.md](provider-verification.md#open-items):

- **IMD** — key transport (header or query parameter) is not documented;
  `WIND_SPEED` units and the `WEATHER_CODE` table are not documented; the
  state-id table is not published.
- **Mappls** — whether traffic flow or traffic-event APIs exist for our
  account; product pages advertise them, public docs publish no endpoint.
  Predictive Routing must be enabled on the key (a live test on 2026-09-27
  returned `ASSET_ACCESS_DENIED` for a key that works with other Mappls
  routing resources).
- **RainViewer** — commercial terms, or a licensed alternative radar source.
  Nowcast frames are mentioned in the docs but the live index returned none, so
  they are not used.
- **Open-Meteo** — commercial subscription before production use.
- **OpenWeatherMap** — whether its `coord` response field echoes the exact query point or a nearby station/city location is undocumented either way.
- **WeatherAPI.com** — `precip_mm`'s accumulation window is undocumented; whether its India alert coverage exists at all.
- **NDMA Sachet** — why its polygon-geometry endpoint returns 403 from this environment; exact-point matching would replace the current state-level approximation if it were reachable.
- **TomTom** — account terms and quotas (account-specific; not recorded).
- **KSNDMC** — a data-access agreement.

No pricing or coverage claims are made here; they depend on contracts not yet
in place.

---

## 9. Sources disabled for lack of credentials or authorisation

| Source | Status without setup | What unblocks it |
|---|---|---|
| TomTom traffic + incidents | `misconfigured`; mock used instead in `auto` | `ANDON_TOMTOM_API_KEY` |
| Mappls corridor ETAs | `misconfigured` | `ANDON_MAPPLS_ACCESS_TOKEN` with Predictive Routing enabled |
| IMD weather | `misconfigured` | `ANDON_IMD_API_KEY` plus the key transport IMD issues with it |
| RainViewer radar | `disabled` (licence) | `ANDON_RAINVIEWER_TERMS_ACKNOWLEDGED=true`, only for personal or educational use |
| KSNDMC (Karnataka) | `disabled` | No API published; site terms restrict commercial and decision-making use. Needs a data-access agreement. |
| Mappls traffic events | `disabled` | No published endpoint; confirm with Mappls. |
| Bengaluru Traffic Police | `disabled` | Interactive map only; no machine-readable feed. Not scraped. |
| BMC Disaster Management | `disabled` | Portal only; no machine-readable feed. |

Every one of these appears in the Sources panel with its reason, so the
dashboard always shows what it *cannot* see as well as what it can.

# Restaurant Situation Awareness System

Real-time situation reports for a food-delivery kitchen, from a latitude and a
longitude.

This is not a weather dashboard and not a traffic dashboard. Weather, traffic
and local incidents are evidence. The product answers one question:

> **What is happening around this kitchen right now, how is it changing, and
> what does it mean operationally?**

---

## Quick start

```bash
./run.sh
```

Open <http://127.0.0.1:8000>. That's it — no API keys and no services to
start. The script creates a virtualenv, installs dependencies, builds the
frontend and serves the API and the UI from one process. Evidence is kept in a
local SQLite file (`backend/data/andon.sqlite3`), created on first run.

Out of the box you get **real weather** from Open-Meteo and NOAA METAR
stations, **real roads** from OpenStreetMap, **simulated** traffic and
incidents, and reports written by the deterministic rules engine. Everything
simulated is labelled as such in the UI.

For a fully offline demo with every source simulated:

```bash
ANDON_MODE=mock ./run.sh
```

Other entry points:

```bash
./run.sh dev     # uvicorn --reload on :8000 + Vite dev server on :5173
./run.sh test    # backend test suite
```

Deployment is deliberately unaddressed for now. The app is a single process
that serves both the API and the built UI, so whatever it eventually runs on
only needs Python, the `frontend/dist` bundle, and environment variables.

To add live signals, copy `.env.example` to `.env` and fill in what you have:

| Variable | Unlocks |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude writes the situation report instead of rules |
| `ANDON_TOMTOM_API_KEY` | Live traffic flow on each access road, and road incidents |
| `ANDON_MAPPLS_ACCESS_TOKEN` | Live-traffic travel times along each access road (Predictive Routing must be enabled on the key) |
| `ANDON_IMD_API_KEY` + `ANDON_IMD_AUTH_HEADER` or `ANDON_IMD_AUTH_QUERY_PARAM` | IMD weather stations (IMD does not document how the key is sent) |
| `ANDON_OPEN_METEO_API_KEY` | Open-Meteo's commercial API (the free one is non-commercial) |
| `ANDON_RAINVIEWER_TERMS_ACKNOWLEDGED=true` | RainViewer radar — only for personal or educational use |

Other settings — provider lists, the SQLite path and retention, freshness
policies, change thresholds, the access graph, background polling — are listed
with comments in [`.env.example`](.env.example) and explained in
[docs/data-layer.md](docs/data-layer.md).

Nothing is required. A missing credential downgrades one component and says so
on screen; it never stops the system. A credential that is **present** always
replaces the mock — and if it is rejected or out of quota, the dashboard shows a
red alert naming the source instead of quietly falling back.

---

## What it does

Enter a restaurant name, latitude, longitude and a refresh interval. Each
refresh runs the full pipeline:

```
Access graph        which roads lead to the kitchen (OpenStreetMap)
      ↓
Data sources        Open-Meteo · METAR · IMD · radar · TomTom · Mappls · mocks
      ↓
Evidence            observations / forecasts with provenance, stored in SQLite
      ↓
Normalization       vendor payloads → one internal shape
      ↓
Situation rules     deterministic severity, trend, freshness, confidence
      ↓
Change detection    has anything material moved since the last report?
      ↓
LLM reasoning       Claude reads the combination (skipped if nothing changed)
      ↓
Situation report    narrative + evidence + outlook
      ↓
Web UI              status banner, signal cards, report, trends, history
```

### Signals

**Weather** — takes a *list* of providers, not one. They answer different
questions, and where they disagree that is itself information:

- **Open-Meteo** — a numerical model at your exact coordinates, with a
  short-term outlook. Good for *what's coming*.
- **AWC METAR** — real station observations from NOAA, free and keyless. Good
  for *what is*. Intensity arrives as a band (`-RA` / `RA` / `+RA`) rather than
  a rate, so the provider carries the band instead of inventing millimetres. No
  forecast, so its trend comes from the station's own observation history.

```bash
ANDON_WEATHER_PROVIDERS=open_meteo,awc_metar
```

Each provider gets **its own tile**, tagged `Model` or `Observed`. A
deterministic consolidation picks the one used for scoring:

1. Failed sources drop out.
2. Primary is the highest-confidence source; a real observation beats a model at
   equal confidence. Distance from a station costs confidence — free within
   10 km, one step to 30 km, two beyond — so a far-away station loses to a local
   model on its own.
3. The trend comes from whichever source can actually forecast, since a station
   only knows where things have been.
4. **If they disagree by a severity band, the UI says so and confidence drops a
   step.** No averaging, no silent winner.

That last point is the whole reason to run two: a model can report light rain at
your coordinates while a station 4 km away reports none. The dashboard shows
both and tells you it doesn't know, rather than picking one and sounding sure.

Either way: precipitation band, wind gusts, visibility, and severe-weather
warnings where a source publishes them.

**Traffic** — congestion sampled on each **access corridor**: the roads that
actually lead to the kitchen, derived from OpenStreetMap (the frontage road and
named approaches split by direction, up to six in total). If road data is unavailable it
falls back to configured or radial points and labels them as approximations. The worst approach is weighted above the average, because a single
blocked approach is what actually strands riders.

**Road and local incidents** — waterlogging, flooding, closures, accidents,
construction, local events, with distance and bearing from the restaurant.

Every observation keeps its **source, observation time, distance, freshness and
confidence**, all the way through to the screen.

**Sources panel** — one card per source (including the ones that cannot run,
with the reason). Click a card for everything that source has reported:
health, provenance, current readings, forecasts, incidents and history.

The evidence layer — persistence, source health, spatial relevance, change
detection, and how to add a provider — is documented in
[docs/data-layer.md](docs/data-layer.md).

---

## The two things this system refuses to do

**It does not invent signals.** If a source fails, the signal is marked
unavailable, excluded from the overall score, named in the report as unknown,
and the confidence drops. It is never quietly treated as "fine". Try it:

```bash
ANDON_TRAFFIC_PROVIDER=tomtom ANDON_TOMTOM_API_KEY=invalid ./run.sh
```

The traffic card reads *Source down*, the banner says *"Assessed without
traffic — that signal is unknown, not confirmed clear"*, and the report is
still generated from the remaining signals.

**It does not blur observation, forecast and inference.** Every piece of
evidence carries an epistemic tag that survives to the UI:

| Tag        | Meaning                                                       |
| ---------- | ------------------------------------------------------------- |
| `observed` | Measured or reported. Statable as fact.                        |
| `forecast` | A prediction. Always attributed as one.                        |
| `inferred` | The engine's own deduction. Never presented as a report.       |

Example of the third: sustained heavy rain makes waterlogging plausible, so the
engine raises a **waterlogging risk** facet — tagged `inferred`, capped at
medium severity so it can never outrank an actual report, and phrased as *"no
feed reports standing water, but rainfall is running at 14.2 mm/h."* If a feed
*does* report waterlogging, the inference is dropped in favour of the
observation.

---

## Division of labour: rules vs. LLM

Classification and arithmetic are deterministic. The LLM never decides whether
8.2 mm/h counts as heavy rain.

`backend/andon/engine/rules.py` holds every threshold in one place:

| Signal     | Bands                                                            |
| ---------- | ---------------------------------------------------------------- |
| Rain       | 0 → none · <2.5 → low · <7.6 → medium · <25 → high · ≥25 → severe (mm/h); a source-reported band wins over a derived one |
| Wind gusts | <25 → none · <40 → low · <62 → medium · <89 → high (km/h)         |
| Visibility | ≥5 km → none · ≥2 km → low · ≥1 km → medium · ≥500 m → high       |
| Congestion | <0.15 → none · <0.35 → low · <0.55 → medium · <0.75 → high        |
| Incidents  | category severity, attenuated by distance from the restaurant     |

The overall level combines available signals so that the peak dominates and the
others compound: one heavy signal alone reads *moderate*, two read *high*,
three read *severe*. Unavailable signals are excluded, not counted as zero.

The LLM gets the *already classified* signals and their evidence, and does the
part rules are bad at: reading the combination, saying what it means for a
kitchen and its riders, and being explicit about what is not known. Its output
is a validated JSON schema (`output_config.format`), not prose to be parsed.

When the LLM is unavailable for any reason — no key, rate limit, timeout,
schema violation — a deterministic writer produces the same report structure
and the UI states who wrote it. A situation-awareness system that goes dark
when its narrator is unavailable has failed at its one job.

---

## Cost control

Two mechanisms, both visible in the API response:

**Provider caching.** Each source sits behind a per-location TTL cache with
single-flight, so a 1-minute auto-refresh does not become a 1-minute external
API call rate. Cached responses keep their original `observed_at`, so the UI
shows the true age of the data, not the age of our copy.

**Material-change detection.** After normalization the situation is fingerprinted
over the facts that would change what a report *says* — severity bands, trend
directions, active incident categories, which sources are missing. Rain drifting
from 8.1 to 8.4 mm/h does not buy another LLM call; crossing into a new band
does. Reports are also regenerated after `ANDON_LLM_MAX_REPORT_AGE_S` (default
30 min) even when nothing moved.

`report.reused` and `report.reuse_reason` report the decision on every response.

A generated report is roughly 2k input and 500 output tokens. Most refreshes
generate nothing at all.

The UI plays its part: auto-refresh is **suppressed while the browser tab is
hidden**, and fires on return if the interval elapsed.

---

## Architecture

```
backend/andon/
├── domain/          enums, provider payloads, situation models  ← the vocabulary
├── providers/       one class per vendor, behind four abstractions
│   ├── base.py         Weather · Radar · Traffic · Incident providers
│   ├── cache.py        TTL + single-flight wrapper
│   ├── registry.py     the only module that knows which vendor is in use
│   ├── disabled.py     sources with no authorised access path, with the reason
│   ├── scenario.py     shared clock driving all mock providers coherently
│   ├── weather/        open_meteo · awc_metar · imd · mock
│   ├── radar/          rainviewer · mock
│   ├── traffic/        tomtom · mappls · mock
│   └── incidents/      tomtom · mock
├── evidence/        canonical evidence, SQLite store, access graph, spatial
│                    relevance, freshness, source health, change detection
├── engine/
│   ├── rules.py        every threshold, pure functions
│   ├── normalize.py    provider payloads → one situation model
│   └── change.py       fingerprint + regenerate-or-reuse decision
├── reasoning/
│   ├── prompt.py       system prompt + output schema
│   ├── claude.py       Anthropic client
│   └── fallback.py     deterministic writer
├── history.py       situation snapshots (SQLite)
├── service.py       the pipeline, wired together
└── api/routes.py    REST surface
```

Adding a signal means adding a provider and a `SignalAssessment`. Replacing a
vendor means writing one class in `providers/`. The UI operates purely on the
normalized model and never learns a vendor's name.

Single process, single deployable. No microservices, no queue; one SQLite
file.

### API

| Endpoint                                     | Purpose                              |
| -------------------------------------------- | ------------------------------------ |
| `GET /api/situation?lat=&lon=&name=&force=`  | Full situation report for a location |
| `GET /api/history?lat=&lon=&minutes=`        | Recent snapshots                     |
| `GET /api/status`                            | Which providers and model are active |
| `/api/restaurants/...`                       | Evidence layer: register, refresh, evidence bundle, sources, changes, history, incidents, access graph — see [docs/data-layer.md](docs/data-layer.md#api) |
| `GET /api/health`                            | Liveness                             |
| `GET /docs`                                  | OpenAPI                              |

`force=true` bypasses the provider caches; it re-runs the LLM only if the
situation moved or the last report is over a minute old.

---

## Trying the failure and severity paths

Mock providers are driven by one shared intensity curve, so when mock weather
says heavy rain, mock traffic and mock incidents agree with it.

```bash
ANDON_MODE=mock ./run.sh                                    # the demo scenario, fully offline
ANDON_WEATHER_PROVIDERS=mock ANDON_MOCK_SCENARIO=storm ./run.sh
```

`ANDON_MODE=mock` runs a fixed demo: moderate rain, a forecast model that
over-reads it (a real source conflict), traffic worsening on one approach,
waterlogging on another, an accident appearing after ~3 minutes, and one
traffic feed that is down.

| `ANDON_MOCK_SCENARIO` | Behaviour                                          |
| --------------------- | -------------------------------------------------- |
| `calm`                | Dry, light traffic, only distant roadworks          |
| `deteriorating`       | Ramps from clear to severe over ~20 min *(default)* |
| `storm`               | Heavy rain, gridlock, waterlogging and closures     |
| `clearing`            | Severe conditions easing over ~20 min               |
| `demo`                | The mixed demo above (forced by `ANDON_MODE=mock`)  |

Leave `ANDON_WEATHER_PROVIDERS` unset and only traffic and incidents follow the
scenario — real weather keeps arriving from Open-Meteo.

Trends need two readings: traffic trend and overall trend appear from the
second refresh onward, and the UI says so rather than showing a fabricated
arrow. The history strip fills in as refreshes accumulate.

---

## Tests

```bash
./run.sh test
```

231 tests, no network and no credentials required. They cover the threshold
tables, the severity-combination rules, trend classification, the honesty
guarantees (dead source excluded from the score, inference never labelled as
observation), fingerprint stability, secret redaction in provider errors,
caching, mock plausibility, Open-Meteo parsing against a captured payload,
structured-log integrity, METAR band and visibility parsing, multi-source
consolidation and disagreement, static-asset path traversal, the evidence
layer (persistence across restarts, spatial relevance, freshness, source health,
change detection, access-graph derivation from a captured OSM payload), the
IMD, METAR, radar, TomTom and Mappls adapters, and the HTTP surface end to end.

---

## Known limits of this prototype

- **History is a local SQLite file.** It survives restarts and is pruned after
  `ANDON_RETENTION_DAYS`. Fine for one process; several instances would each
  keep their own.
- **Report cache is in-process**, so several instances behind a load balancer
  would each generate their own report.
- **Open-Meteo is a numerical model, not a station reading**, and publishes no
  severe-weather warnings. Both facts are surfaced — the source is rated medium
  confidence and declares the alert gap — rather than hidden.
- **TomTom has no waterlogging category**, only flooding. The incident provider
  declares that coverage gap, so the UI can say "not covered by this source"
  instead of implying "nothing reported".
- **Access corridors are a grouping of mapped roads, not a routing analysis.**
  When OpenStreetMap is unreachable, traffic falls back to radial points on a
  900 m radius, labelled as approximations.
- **A station observation is real, but it is real where the station is.** METAR
  stations are airports; distance from the restaurant is reported on every
  reading and costs confidence beyond 10 km, and past 30 km the card says so
  outright. In Bengaluru the nearest station to HSR Layout is ~4 km, which is
  fine; elsewhere it can be tens of kilometres.

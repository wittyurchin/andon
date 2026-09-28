# Provider verification record

What each external source actually documents, checked on **2026-09-27** against
the local copies in [`documentation/`](../documentation/README.md). This file
is the reason an adapter looks the way it does. If a fact is not listed as
verified here, the code must not depend on it.

Legend: ✅ verified from primary docs or a live call · ⚠️ verified, with a
constraint that affects us · ❌ not documented / blocked · 🔒 needs credentials
we do not have.

---

## Weather

### Open-Meteo — *forecast / model evidence*

| | |
|---|---|
| ✅ Endpoint | `https://api.open-meteo.com/v1/forecast`, variables `current`, `minutely_15`, `past_minutely_15`, `forecast_minutely_15` (live call returned data) |
| ✅ Grid point | Response `latitude`/`longitude` is the **model grid cell**, not the requested point — it can be kilometres away (3.4 km for central Bengaluru). The adapter records it. |
| ⚠️ Licence | Free tier: **non-commercial use only**, <10,000 calls/day (`open-meteo/terms.html`). |
| ✅ Commercial path | `customer-api.open-meteo.com`, same syntax plus `&apikey=` (`open-meteo/pricing.html`). |
| ✅ Attribution | CC-BY 4.0 (`open-meteo/licence.html`). |
| Classification | **Forecast/model.** Never an observation, including "past" blocks (those are model analysis). |
| Adapter behaviour | Uses the customer endpoint when `ANDON_OPEN_METEO_API_KEY` is set; otherwise the free endpoint, and source health carries `licence: free tier, non-commercial only`. |

### NOAA Aviation Weather Center METAR — *station observation evidence*

| | |
|---|---|
| ✅ Endpoint | `/api/data/metar` (`noaa-awc/openapi.yaml`) |
| ✅ `bbox` | `lat0,lon0,lat1,lon1` — matches the adapter |
| ✅ `hours` | "Hours back to search", default 1.5 |
| ✅ Rate limit | 100 requests/minute; set a custom user agent (`noaa-awc/data_api.html`) |
| ✅ Coverage near HSR Layout, Bengaluru | VOBG (HAL Airport) 4.3 km, reporting half-hourly (live call) |
| Classification | **Observation**, at the station's location — distance is always recorded. |
| Intensity | Qualitative band (`-RA`/`RA`/`+RA`); no rate is published, so none is stored. |

### IMD (India Meteorological Department) — 🔒 *adapter built, disabled until credentialed*

| | |
|---|---|
| ✅ Endpoints | `https://api.imd.gov.in/api/v1/aws_data?id=` / `?sid=`, `current_wx?id=`, `districtnowcast`, `stationnowcast`, `districtwarning`, … (`imd/api_reference.html`) |
| ✅ `aws_data` fields | `ID`, `STATION`, `CURR_TEMP`, `RH`, `WIND_SPEED`, `MSLP`, `WEATHER_CODE`, `Latitude`, `Longitude` |
| ✅ Auth required | Live call → `401 {"error":"API key missing"}` for every endpoint tried |
| ❌ Key transport | **Not published.** Neither the reference nor the portal says which header or query parameter carries the key. |
| ❌ Units | Units for `WIND_SPEED`, and the code table for `WEATHER_CODE`, are not documented. |
| ❌ Portal page | `mausam.imd.gov.in/responsive/apis.php` (which mentioned IP whitelisting) now returns 404. |
| Adapter behaviour | Requires `ANDON_IMD_API_KEY` **and** an explicit `ANDON_IMD_AUTH_HEADER` or `ANDON_IMD_AUTH_QUERY_PARAM` — we do not guess. Missing → `misconfigured`. 401/403 → `unauthorized`. Undocumented units/codes are stored verbatim with `unit: null`, never converted. |

### OpenWeatherMap — 🔒 *adapter built, needs `ANDON_OPENWEATHERMAP_API_KEY`*

| | |
|---|---|
| ✅ Endpoint | `GET https://api.openweathermap.org/data/2.5/weather?lat=&lon=&appid=&units=metric` (`openweathermap.org/current`) |
| ✅ Units | `units=metric` gives Celsius, but **wind speed/gust stay metre/second** even under `metric` — only `imperial` changes them (to mph). Converted to km/h by us. |
| ✅ `rain.1h` | mm/h, and **absent (not zero) when there is no rain** ("these weather phenomena are just not happened for the time of measurement") — a missing field is recorded as no precipitation, not fabricated. |
| ✅ Data basis | Own docs: "collected and processed from different sources such as global and local weather models, satellites, radars and a vast network of weather stations" — a blend, not a single station's raw report. Extracted as model/forecast evidence, same rule as Open-Meteo. |
| ❌ Alerts | This endpoint documents none. Alerts exist only on the separate One Call 3.0/4.0 product. |
| ⚠️ One Call gated behind billing | One Call requires the "One Call by Call" subscription, which needs a card on file even for its free 1,000 calls/day (openweathermap.org/api/one-call-3, openweathermap.org/price, and user reports of billing details being required at signup). Out of scope while this stays a no-billing-surprises default. |
| ✅ Licence | Free tier **permits commercial use** (attribution obligatory above the free plan, per `openweathermap.org/faq`) — more permissive than Open-Meteo's non-commercial-only free tier. |
| ✅ Rate limit | 429 on exceeding the day/month quota (FAQ), not a per-second limiter; the account can be throttled for "a couple of hours to several days" after repeated overage. |
| ❌ Coordinate snapping | Whether the response's `coord` echoes the exact query point or a nearby station/city is not documented either way — no resolution or "grid cell" claim is made, only plain distance. |
| Adapter behaviour | `ANDON_OPENWEATHERMAP_API_KEY` unset → `misconfigured`. 401 → `unauthorized`. |

### KSNDMC (Karnataka) — ❌ *adapter boundary only, disabled*

| | |
|---|---|
| ❌ API | None published (`ksndmc/*.html`). The Varunamitra portal and Megha Sandesha app are web/app front-ends, not APIs. |
| ⚠️ Terms | The KSNDMC rainfall page's disclaimer says the data should not be used for commercial or decision-making purposes and to contact KSNDMC for validated data. |
| Path to enable | Written data-access agreement with KSNDMC (office@ksndmc.org). Until then the adapter reports `disabled` and is never called. Scraping is out of scope by rule. |

### Radar — RainViewer ⚠️ *adapter built, disabled by licence*

| | |
|---|---|
| ✅ Index | `https://api.rainviewer.com/public/weather-maps.json` (live call: 13 past frames, newest 10 min old) |
| ✅ Tiles | `{host}{path}/{size}/{z}/{x}/{y}/{color}/{options}.png`, max zoom 7, `options` = `{smooth}_{snow}` |
| ✅ Colour table | Scheme 2 "Universal Blue" is the only scheme offered; exact RGBA ↔ dBZ table published as CSV. With `options=0_0` (no smoothing) pixels decode by exact lookup. |
| ⚠️ Terms | "Available for **personal and educational use only**"; commercial use by bespoke agreement. Attribution required. No SLA. |
| ⚠️ Nowcast | The docs mention nowcast frames, but the live index returned **none**. Not relied on. |
| Adapter behaviour | Implemented and tested, **off by default** (`ANDON_RADAR_PROVIDER=mock|rainviewer|none`). Reflectivity (dBZ) is the observation; the intensity band derived from it is labelled a derivation using an engineering convention, not a provider fact. |

---

## Traffic and incidents

### TomTom — 🔒 *adapter built, needs `ANDON_TOMTOM_API_KEY`*

| | |
|---|---|
| ✅ Flow Segment Data v4 | `/traffic/services/4/flowSegmentData/{style}/{zoom}/{format}?key&point&unit` — `frc`, `currentSpeed`, `freeFlowSpeed`, `currentTravelTime`, `freeFlowTravelTime`, `confidence` (0–1), `roadClosure`, `coordinates` (`tomtom/flow-segment-data.html`). Zoom controls which road classes are matchable; we use 18 (zoom 10 snapped HSR probes to an arterial ~1 km away, zoom 18 matched within 1–4 m, tested 2026-09-27) |
| ✅ Incident Details v5 | `bbox` is `minLon,minLat,maxLon,maxLat`; `iconCategory` 0–11,14; `magnitudeOfDelay` 0 unknown, 1 minor, 2 moderate, 3 major, 4 undefined (`tomtom/incident-details.html`) |
| 🐛 Fixed | The previous request did not include `id` in `fields`, so every incident lost its source record id. |
| ❌ Pricing / quotas | Not recorded here; account-specific. |

### Mappls (MapmyIndia) — ⚠️ *partial: corridor ETA only*

| | |
|---|---|
| ❌ Traffic flow / events REST API | **Not published.** Product pages advertise traffic flow and event feeds, but no endpoint is documented publicly; the GitHub REST docs contain none. Likely enterprise-only. |
| ✅ Auth (current, since Aug 2025) | Static key from the Mappls console, sent as the `access_token` **query parameter** (`mappls/rest-apis-main/predictive-routing-api.md`). |
| ⚠️ Legacy auth | OAuth client-credentials at `outpost.mappls.com/api/security/oauth/token` is documented on the `auth-legacy` branch only. Not used. |
| ✅ Predictive Routing | `https://route.mappls.com/routev2/direction/route?locations=lon,lat;lon,lat&profile=driving&speedTypes=traffic&date_time=0,""&access_token=` → `trip.summary.length` (km), `trip.summary.time` (s). India only. |
| ⚠️ Semantics | `speedTypes=optimal` is "ETA acc. to current time" — **not free-flow**. So a Mappls observation is "live-traffic ETA along a corridor", and any comparison is labelled "vs Mappls optimal ETA", never "vs free-flow". |
| ⚠️ Per-key API access | Live test 2026-09-27: a valid console key got `401 ASSET_ACCESS_DENIED` from Predictive Routing (`routev2`) and from `route_traffic`, while `route_adv` and `route_eta` (`route.mappls.com/route/direction/{resource}/driving/…?access_token=`) returned 200. APIs are enabled per key in the console. `route_eta` is documented (`auth-legacy/routing-api.md`) as "updated duration of a route considering live traffic", applied to the default route. |
| Adapter behaviour | Corridor-level traffic evidence (one route per approach corridor into the restaurant). Mappls incident adapter: `disabled`, no documented API. |

### Authority incident feeds — ❌ *adapter boundary only, disabled*

| Source | Finding |
|---|---|
| Bengaluru Traffic Police | Interactive map only; no documented machine-readable feed. Not scraped. |
| BMC Disaster Management (Mumbai) | Portal (`authorities/bmc-disaster-management.html`) with no documented feed. |

---

## Derived / first-party

### OpenStreetMap via Overpass — ✅ *access graph source*

| | |
|---|---|
| ✅ Licence | ODbL; must credit "© OpenStreetMap contributors" and note ODbL (`openstreetmap/copyright.html`) |
| ✅ Public instance policy | ~10,000 requests/day, <1 GB/day (`openstreetmap/overpass-commons.html`) |
| Our usage | One query per restaurant when its access graph is built, then cached in SQLite. Far inside the limit. |
| Classification | **Derived dataset.** Approach corridors are our grouping of OSM roads, labelled as such. |

---

## Open items

1. IMD: obtain a key and the key transport from IMD; confirm `WIND_SPEED` units and the `WEATHER_CODE` table.
2. KSNDMC: data-access agreement for commercial/operational use.
3. Mappls: confirm with Mappls whether traffic flow/event APIs exist for our account tier; enable Predictive Routing on the key (or adopt `route_eta`).
4. RainViewer: commercial terms, or an alternative licensed radar source (IMD DWR products are listed as "radar/specialized information where API access permits").
5. Open-Meteo: commercial subscription before production use.
6. TomTom: account, quotas and terms.

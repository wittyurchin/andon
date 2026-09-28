/** Plain-language "what is this source" reference shown in the Info tab.
 *
 * One entry per backend source id (see backend/andon/providers/*), grounded
 * in that provider's own code comments and docs/provider-verification.md —
 * nothing here should say more than the adapter actually knows.
 */
export interface SourceInfoEntry {
  /** One or two sentences: what this source fundamentally is. */
  summary: string
  /** How the reading gets here. */
  how: string
  /** What it actually reports. */
  reports: string[]
  /** What it deliberately does not do, or where it falls short. */
  limits: string[]
  /** A note on how much this source's readings are trusted, and why. */
  confidence?: string
}

export const SOURCE_INFO: Record<string, SourceInfoEntry> = {
  'awc-metar': {
    summary:
      'Real station observations from NOAA’s Aviation Weather Center — a trained observer or ' +
      'automated station at a known airport, not a model. This is the "what is happening right now" weather source.',
    how:
      'Free, no API key. Every station reporting within the configured search radius (120 km by default) is ' +
      'fetched, the nearest one to the restaurant is picked, and its last 3 hours of reports are kept so there is ' +
      'a real trend from the first refresh.',
    reports: [
      'Precipitation as a qualitative band (e.g. "light rain") from the METAR code, not a millimetre rate — ' +
        'a rate is never invented',
      'Wind speed, and gust only when the station actually reports one (never copied from sustained wind)',
      'Visibility and temperature, when reported',
      'The raw METAR string, so a reading can be checked against the original report',
    ],
    limits: [
      'No forecast — this source only ever reports what already happened, so its trend comes from its own past readings',
      'Stations are sparse and are usually airports, sometimes tens of kilometres from the restaurant',
    ],
    confidence:
      'High engineering-default confidence (0.9) because it is a real observation — but it is an observation ' +
      'at the station, not at the restaurant. Its distance is shown on every reading and reduces confidence the farther it is.',
  },

  'open-meteo': {
    summary:
      'A numerical weather model evaluated at the restaurant’s coordinates, not a station reading. Chosen ' +
      'because it needs no credentials, so the default setup always has at least one real data source.',
    how:
      'Free tier, no key (licensed for non-commercial use only — a commercial key switches to a separate ' +
      'paid endpoint). Queried for current, recent and short-term forecast blocks at the model’s grid resolution.',
    reports: [
      'Precipitation rate, wind, visibility and temperature for the model grid cell nearest the restaurant',
      'A short-term forecast, so it is the only weather source this app can show a genuine outlook from',
    ],
    limits: [
      'Every value it produces is model output, including its "current" and "past" blocks — stored as a ' +
        'forecast, never as an observation, because a model’s value for now is still the model’s opinion',
      'No severe-weather alert feed',
      'The response’s coordinates are the model grid cell, which can sit kilometres from the exact point requested — recorded rather than hidden',
    ],
    confidence:
      'Lower engineering-default confidence (0.6) than a station, because it is a prediction for a grid cell, not a measurement.',
  },

  openweathermap: {
    summary:
      'A second, independent weather vendor’s blend of models, stations, radar and satellites — not the same ' +
      'model as Open-Meteo, so agreement or disagreement between them is itself evidence.',
    how:
      'Free "Current Weather" endpoint, no card required. Own docs describe the reading as collected and ' +
      'processed from multiple sources rather than one station’s raw report, so — like Open-Meteo — it is treated ' +
      'as model evidence, not an observation.',
    reports: [
      'A numeric precipitation rate (mm/h), unlike METAR’s qualitative band — so it can disagree with METAR in a different way than Open-Meteo does',
      'Wind, visibility and temperature for its reported location',
    ],
    limits: [
      'This endpoint documents no severe-weather alerts at all. OpenWeatherMap does publish alerts, but only on a separate product that requires billing details on file even for its free daily quota — out of scope while this app avoids billing surprises',
      'Whether its reported coordinates are the exact point requested or a nearby station/city is not documented either way, so no resolution claim is made about it',
      'A missing rain reading is recorded as no precipitation, since the provider documents that as its meaning — not assumed to be zero by us',
    ],
    confidence:
      'Same engineering-default confidence as Open-Meteo (0.6): a blended estimate for a location, not a direct measurement.',
  },

  weatherapi: {
    summary:
      'A third, independent weather vendor — already in production use elsewhere in a rain-decision system that ' +
      'combines it with official government alerts and satellite data.',
    how:
      'Free tier, no card required, commercial use permitted. Its docs don’t state whether a reading is a station ' +
      'report or a blended estimate, and the payload carries no station identity, so — like the other two model ' +
      'sources — it is treated as model evidence, not an observation.',
    reports: [
      'Wind, visibility and temperature in the units this app already uses internally, so no conversion is needed',
    ],
    limits: [
      'Its precipitation field (precip_mm) has no documented accumulation window — unlike Open-Meteo’s or ' +
        'OpenWeatherMap’s explicit "per hour" — so it is stored as reported, not treated as a rate or banded into a severity level',
      'Its alerts feature is documented for the USA, UK and Europe, with no confirmation for India, and is not requested here',
    ],
    confidence:
      'Same engineering-default confidence as the other model sources (0.6): a blended estimate for a location, not a direct measurement.',
  },

  'ndma-sachet': {
    summary:
      'Official India government disaster alerts — the same national feed a real production system (a ' +
      'restaurant chain’s rain-decision sheet) uses for its severe-weather warnings.',
    how:
      'Free public feed, public domain, no key. The restaurant’s state is resolved once (via OpenStreetMap, ' +
      'cached — a restaurant’s state never changes) and checked against each active alert’s sender and area text. ' +
      'Severity is graded by keyword (heavy/moderate/watch), not by the feed’s own label, because states use that ' +
      'label inconsistently — a same-day outlook is downgraded to a watch rather than treated as imminent.',
    reports: [
      'Active official alerts for the restaurant’s state — event, headline, severity, and when it started and expires',
    ],
    limits: [
      'Matched at STATE level, not district. The feed publishes an exact polygon for each alert, but that ' +
        'endpoint was unreachable when this was built (HTTP 403) — every alert produced here says "state-level, ' +
        'exact area not confirmed" in its own headline, so this is never mistaken for a precise local warning',
      'Outside India, this always reports no alerts, by construction — no Indian state name will ever match',
      'River-level flood forecasts (Central Water Commission alerts) are graded as a watch, never a rain alert',
    ],
  },

  'imd-aws': {
    summary:
      'India Meteorological Department automatic weather stations. The adapter is built, but IMD does not ' +
      'publish how its API key is meant to be sent, so it needs manual configuration before it can run.',
    how:
      'Documented endpoint (`aws_data?sid=`), but the key transport (header vs. query parameter, and its name), ' +
      'the state-id table, and the units for wind speed and temperature are all undocumented — so they must be set explicitly rather than guessed.',
    reports: [
      'Whatever the station returns, stored verbatim under "reported" — raw fields, not banded or classified, because their units are unknown',
    ],
    limits: [
      'No rain band is derived from IMD’s weather code, because the code table is not published',
      'No observation timestamp field exists in the response, so readings are marked by receipt time, not source time',
      'Disabled until `ANDON_IMD_API_KEY` and its transport are both configured',
    ],
  },

  ksndmc: {
    summary:
      'Karnataka’s state rain-gauge and weather-station network. No API is published, only a public web portal.',
    how: 'Not called — there is no documented, machine-readable feed to call.',
    reports: [],
    limits: [
      'The site’s own disclaimer restricts commercial and decision-making use of the data',
      'Would need a formal data-access agreement with KSNDMC before it could be wired in',
    ],
  },

  rainviewer: {
    summary:
      'Radar reflectivity imagery — the only weather source here that observes precipitation spatially around ' +
      'the restaurant, rather than at one point.',
    how:
      'Reads RainViewer’s published tile index and decodes the radar PNG using its documented colour-to-dBZ ' +
      'table, sampling the cell the restaurant sits in.',
    reports: [
      'A reflectivity value (dBZ) for the cell containing the restaurant, and the strongest nearby cell, so a ' +
        '"dry here but raining a kilometre away" pattern is visible',
    ],
    limits: [
      'Disabled by default: RainViewer’s free API is licensed for personal and educational use only, and is ' +
        'only called once that is explicitly acknowledged',
      'Resolution is coarse — about 1.2 km per pixel — so a reading means "somewhere in this ~1.2 km cell", not an exact point',
      'Some colours in the published table stand for a dBZ range rather than one exact value; both bounds are kept',
    ],
  },

  'tomtom-flow': {
    summary:
      'Live traffic speed on the roads that actually lead to the restaurant, sampled per access corridor rather than as one city-wide figure.',
    how:
      'Each corridor from the restaurant’s access graph is probed against TomTom’s Flow Segment Data API. ' +
      'TomTom returns whichever road segment is nearest the probe point; the gap between that segment and the intended corridor is measured and kept.',
    reports: [
      'Current speed and free-flow speed for each access road, and the congestion index derived from their ratio',
      'A match-distance figure and warning when TomTom’s matched segment may be a different road than intended',
    ],
    limits: [
      'Needs `ANDON_TOMTOM_API_KEY`',
      'A rejected key or an exhausted plan is reported as exactly that (see its Status tab), never silently swapped for a mock',
    ],
  },

  'tomtom-incidents': {
    summary: 'Road incidents — accidents, closures, congestion — within range of the restaurant, from TomTom.',
    how: 'Queried by a bounding box around the restaurant; each incident carries its own category, delay and a source record id.',
    reports: [
      'Incident type, description, distance and bearing from the restaurant, whether it sits on an access corridor, and a probability-of-occurrence based confidence',
    ],
    limits: [
      'TomTom has a flooding category but no separate waterlogging category — the rules engine treats waterlogging as "not covered by this source", never as "confirmed absent"',
      'Needs `ANDON_TOMTOM_API_KEY`',
    ],
  },

  'mappls-route': {
    summary:
      'Live-traffic travel time along each access corridor into the restaurant, from Mappls’ Predictive Routing API — corridor-level evidence, not segment-level.',
    how:
      'Mappls publishes no traffic-flow or traffic-event REST API, so this adapter instead routes along each ' +
      'corridor and reads back the live-traffic ETA. A second "optimal" ETA is fetched for comparison, when enabled.',
    reports: [
      'A live travel time for each access corridor, and (optionally) a comparison ETA labelled "vs Mappls optimal ETA"',
    ],
    limits: [
      'The "optimal" comparison figure is Mappls’ current-time ETA, not a free-flow baseline — it is never read as one',
      'Needs `ANDON_MAPPLS_ACCESS_TOKEN`, and Predictive Routing must be enabled for that key specifically',
      'A 403 from Mappls is documented as a daily/hourly limit, so it is reported as a quota problem rather than an authorization failure',
    ],
  },

  'mappls-events': {
    summary: 'Mappls advertises traffic-event feeds, but no endpoint for them is published anywhere Mappls documents its REST APIs.',
    how: 'Not called — there is nothing documented to call.',
    reports: [],
    limits: ['Would need confirmation directly from Mappls that this API exists for this account tier before it could be built'],
  },

  btp: {
    summary: 'Bengaluru Traffic Police advisories — an interactive map only, no machine-readable feed.',
    how: 'Not called.',
    reports: [],
    limits: ['No documented API; not scraped'],
  },

  'bmc-dm': {
    summary: 'Mumbai’s BMC Disaster Management portal (waterlogging, road incidents) — a portal only, no machine-readable feed.',
    how: 'Not called.',
    reports: [],
    limits: ['No documented API; not scraped'],
  },

  'mock-weather': {
    summary: 'A simulated numerical model, standing in for Open-Meteo when no real model source is configured.',
    how: 'Driven by a shared scenario clock so it moves coherently with the other mock sources over the session.',
    reports: ['A rain value for a simulated grid cell near the restaurant, plus a short mock forecast'],
    limits: ['Not real data — in the demo scenario it deliberately over-reads rain relative to the mock station, to exercise source disagreement'],
  },

  'mock-station': {
    summary: 'A simulated weather station, standing in for METAR when no real station source is configured.',
    how: 'A fixed simulated point a few kilometres away, reporting a qualitative band on a half-hourly cadence.',
    reports: ['A rain band and basic conditions at a simulated point, with simulated report history'],
    limits: ['Not real data'],
  },

  'mock-radar': {
    summary: 'A simulated reflectivity field, standing in for RainViewer.',
    how: 'Shares the scenario clock with the other mocks. In the demo scenario a heavier simulated cell sits south-west of the kitchen.',
    reports: ['A reflectivity value for the restaurant’s cell and for nearby cells'],
    limits: ['Not real data'],
  },

  'mock-traffic': {
    summary: 'Simulated traffic, standing in for TomTom or Mappls when no real traffic source is configured.',
    how: 'Samples the same access corridors a real provider would, moving with the shared scenario (weather, meal-time peaks, one approach steadily worsening in the demo scenario).',
    reports: ['Simulated current/free-flow speed per access corridor. Road names come from the real access graph when available — never invented'],
    limits: ['Not real data'],
  },

  'mock-traffic-feed-b': {
    summary: 'A second simulated traffic feed that is always down, purely to demonstrate provider independence and unavailable-source handling.',
    how: 'Always fails.',
    reports: [],
    limits: ['Deliberately never succeeds — this is what a real failing source looks like on the dashboard'],
  },

  'mock-incidents': {
    summary: 'Simulated road incidents, standing in for TomTom or Mappls events.',
    how: 'Placed on the restaurant’s real access corridors, so spatial relevance is exercised the same way real incidents would be. In the demo scenario: waterlogging on one approach from the start, an accident on another appearing a few minutes in.',
    reports: ['Simulated incidents with type, distance and corridor membership'],
    limits: ['Not real data'],
  },
}

export const ROAD_NETWORK_INFO: SourceInfoEntry = {
  summary:
    'Which roads actually lead to the kitchen — the Restaurant Access Graph — used to decide where traffic ' +
    'and incidents are actually sampled, instead of five arbitrary points on a circle.',
  how:
    'Built once per restaurant from OpenStreetMap (via Overpass): nearby drivable roads are fetched, the nearest ' +
    'is the frontage road, and major roads are grouped into named corridors split by compass direction. The graph is ' +
    'cached and rebuilt periodically, or immediately if OpenStreetMap data was unavailable at build time.',
  reports: [
    'One corridor per named approach road, with its road class, length and the probe point traffic is sampled at',
  ],
  limits: [
    'If OpenStreetMap cannot be reached, this falls back to operator-configured points, then to a radial ' +
      'approximation (evenly spaced points on a circle) — always labelled as an approximation when that happens',
  ],
}

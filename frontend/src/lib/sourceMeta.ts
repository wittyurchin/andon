/**
 * Static facts about each weather source's *provider*, not its readings —
 * pricing and what it's structurally capable of reporting. This is chrome,
 * not evidence: it never varies by response, so unlike Observation/
 * SourceHealthRecord it is not computed server-side. Every entry below is
 * grounded in the adapter file or docs/provider-verification.md cited next
 * to it — nothing here is a guess.
 *
 * Weather sources only, per the current Sources panel scope. Add an entry
 * here whenever a new weather provider is added (see docs/data-layer.md
 * "how to add a new provider").
 */

export type Pricing = 'free' | 'paid' | 'unpublished' | 'restricted' | 'n/a'

export interface PricingInfo {
  label: string
  tier: Pricing
  note: string
}

/** Three states, not two: a source can report a field without it being usable. */
export type Coverage = 'covered' | 'not_covered' | 'unusable'

export interface CoverageFact {
  coverage: Coverage
  note: string
}

export type CoverageKey =
  | 'precipitation'
  | 'wind'
  | 'gusts'
  | 'visibility'
  | 'temperature'
  | 'forecast'
  | 'alerts'

/** Short labels for the coverage strip. Plain words, not pictograms. */
export const COVERAGE_SHORT: Record<CoverageKey, string> = {
  precipitation: 'Rain',
  wind: 'Wind',
  gusts: 'Gust',
  visibility: 'Vis',
  temperature: 'Temp',
  forecast: 'Fcst',
  alerts: 'Alerts',
}

export const COVERAGE_LABEL: Record<CoverageKey, string> = {
  precipitation: 'Precipitation',
  wind: 'Wind',
  gusts: 'Gusts',
  visibility: 'Visibility',
  temperature: 'Temperature',
  forecast: 'Forecast',
  alerts: 'Severe alerts',
}

interface WeatherSourceMeta {
  pricing: PricingInfo
  // null for sources that are never called (e.g. KSNDMC) — nothing to cover.
  coverage: Record<CoverageKey, CoverageFact> | null
}

const COVERED = (note: string): CoverageFact => ({ coverage: 'covered', note })
const NOT_COVERED = (note: string): CoverageFact => ({ coverage: 'not_covered', note })
const UNUSABLE = (note: string): CoverageFact => ({ coverage: 'unusable', note })

// backend/andon/providers/weather/open_meteo.py
const OPEN_METEO_FREE: PricingInfo = {
  label: 'Free (non-commercial)',
  tier: 'free',
  note: 'Free tier is licensed for non-commercial use only (open-meteo/terms.html).',
}
const OPEN_METEO_PAID: PricingInfo = {
  label: 'Paid (commercial key)',
  tier: 'paid',
  note: 'A commercial API key is configured; requests go to the paid customer-api.open-meteo.com endpoint.',
}
const OPEN_METEO_COVERAGE: Record<CoverageKey, CoverageFact> = {
  precipitation: COVERED('Model rate, mm/h.'),
  wind: COVERED('Sustained speed.'),
  gusts: COVERED('Gust speed.'),
  visibility: COVERED('From the 15-minute series only.'),
  temperature: COVERED('Model estimate.'),
  forecast: COVERED('~1 h of past model blocks plus ~2 h ahead.'),
  alerts: NOT_COVERED("Open-Meteo's free forecast API carries no severe-weather warnings feed."),
}

// backend/andon/providers/weather/awc_metar.py
const METAR_FREE: PricingInfo = {
  label: 'Free',
  tier: 'free',
  note: 'No signup, no key, ever — NOAA Aviation Weather Center is a public feed.',
}
const METAR_COVERAGE: Record<CoverageKey, CoverageFact> = {
  precipitation: COVERED('Reported as a band (-RA/RA/+RA), never a rate — never converted.'),
  wind: COVERED('Sustained speed from the METAR group.'),
  gusts: COVERED('Only when the METAR group itself reports one; never inferred from sustained wind.'),
  visibility: COVERED('Converted from statute miles.'),
  temperature: COVERED('From the METAR group.'),
  forecast: NOT_COVERED('A station reports what is, not what is coming; trend comes from its own history.'),
  alerts: NOT_COVERED('This feed carries no severe-weather warnings.'),
}

// backend/andon/providers/weather/imd.py
const IMD_UNPUBLISHED: PricingInfo = {
  label: 'Pricing not published',
  tier: 'unpublished',
  note: 'IMD requires a key, but no pricing is published anywhere we have verified.',
}
const IMD_COVERAGE: Record<CoverageKey, CoverageFact> = {
  precipitation: NOT_COVERED("IMD's WEATHER_CODE table is undocumented, so no rain band is derived from it."),
  wind: UNUSABLE('WIND_SPEED is reported, but its units are undocumented — stored verbatim, never classified.'),
  gusts: NOT_COVERED('No gust field in the documented schema.'),
  visibility: NOT_COVERED('Not in the documented schema.'),
  temperature: UNUSABLE('CURR_TEMP is reported, but its units are undocumented — stored verbatim, never classified.'),
  forecast: NOT_COVERED('Automatic weather stations report current conditions only.'),
  alerts: NOT_COVERED('No warnings feed.'),
}

// backend/andon/providers/disabled.py: ksndmc_weather()
const KSNDMC_RESTRICTED: PricingInfo = {
  label: 'N/A — disabled',
  tier: 'restricted',
  note: "No documented API or feed; KSNDMC's site disclaimer restricts commercial and decision-making use.",
}

// backend/andon/providers/weather/openweathermap.py
const OWM_FREE: PricingInfo = {
  label: 'Free (commercial ok)',
  tier: 'free',
  note: 'Free Current Weather tier permits commercial use; attribution required above the free plan (openweathermap.org/faq).',
}
const OWM_COVERAGE: Record<CoverageKey, CoverageFact> = {
  precipitation: COVERED('rain.1h, mm/h. Absent means no rain, per its docs.'),
  wind: COVERED('Converted from m/s (documented as m/s even under metric units).'),
  gusts: COVERED('Only when the response includes one.'),
  visibility: COVERED('Metres, capped at 10 km by the provider.'),
  temperature: COVERED('Celsius.'),
  forecast: NOT_COVERED('Only the current-conditions endpoint is called.'),
  alerts: NOT_COVERED('Alerts live on the paid One Call product, which needs a card on file. Not requested.'),
}

// backend/andon/providers/weather/weatherapi.py
const WEATHERAPI_FREE: PricingInfo = {
  label: 'Free (commercial ok)',
  tier: 'free',
  note: '100,000 calls/month free, no card, commercial use permitted (weatherapi.com/pricing.aspx).',
}
const WEATHERAPI_COVERAGE: Record<CoverageKey, CoverageFact> = {
  precipitation: UNUSABLE('precip_mm is reported, but its time window is undocumented. Stored verbatim, never treated as a rate.'),
  wind: COVERED('km/h as reported.'),
  gusts: COVERED('km/h as reported.'),
  visibility: COVERED('Converted from km.'),
  temperature: COVERED('Celsius.'),
  forecast: NOT_COVERED('Only the current-conditions endpoint is called.'),
  alerts: NOT_COVERED('Documented for the USA, UK and Europe, not confirmed for India. Not requested.'),
}

// backend/andon/providers/weather/ndma_sachet.py
const SACHET_FREE: PricingInfo = {
  label: 'Free (public domain)',
  tier: 'free',
  note: 'The feed states its own copyright as public domain. No key.',
}
const SACHET_COVERAGE: Record<CoverageKey, CoverageFact> = {
  precipitation: NOT_COVERED('Alerts only; no rain measurement.'),
  wind: NOT_COVERED('Alerts only.'),
  gusts: NOT_COVERED('Alerts only.'),
  visibility: NOT_COVERED('Alerts only.'),
  temperature: NOT_COVERED('Alerts only.'),
  forecast: NOT_COVERED('Alerts carry their own validity window, not a forecast series.'),
  alerts: COVERED("Official alerts, matched to the kitchen's state, not district (polygon endpoint returned 403)."),
}

const SIMULATED: PricingInfo = {
  label: 'Simulated',
  tier: 'n/a',
  note: 'A mock source — not a real provider, so pricing does not apply.',
}

export const WEATHER_SOURCE_META: Record<string, WeatherSourceMeta> = {
  'open-meteo': { pricing: OPEN_METEO_FREE, coverage: OPEN_METEO_COVERAGE },
  'awc-metar': { pricing: METAR_FREE, coverage: METAR_COVERAGE },
  'imd-aws': { pricing: IMD_UNPUBLISHED, coverage: IMD_COVERAGE },
  ksndmc: { pricing: KSNDMC_RESTRICTED, coverage: null }, // never called — nothing to cover
  openweathermap: { pricing: OWM_FREE, coverage: OWM_COVERAGE },
  weatherapi: { pricing: WEATHERAPI_FREE, coverage: WEATHERAPI_COVERAGE },
  'ndma-sachet': { pricing: SACHET_FREE, coverage: SACHET_COVERAGE },
  // backend/andon/providers/weather/mock.py — mirrors the real source it imitates.
  'mock-weather': { pricing: SIMULATED, coverage: { ...OPEN_METEO_COVERAGE, alerts: COVERED('Simulated warning above a rain threshold.') } },
  'mock-station': { pricing: SIMULATED, coverage: { ...METAR_COVERAGE } },
}

/** Pricing badge for a weather source, dynamic only for Open-Meteo. */
export function pricingFor(sourceId: string, licenceNote: string | null): PricingInfo | null {
  if (sourceId === 'open-meteo') return licenceNote ? OPEN_METEO_FREE : OPEN_METEO_PAID
  return WEATHER_SOURCE_META[sourceId]?.pricing ?? null
}

export function coverageFor(sourceId: string): Record<CoverageKey, CoverageFact> | null {
  return WEATHER_SOURCE_META[sourceId]?.coverage ?? null
}

import { clockTime } from './format'
import type { Applicability, HealthStatus, Observation } from '../types'

export const APPLICABILITY_LABEL: Record<Applicability, string> = {
  at_site: 'at the restaurant',
  local: 'within 1 km',
  nearby: 'within 5 km',
  regional: 'regional',
  distant: 'distant',
  unknown: 'location unknown',
}

export const HEALTH_LABEL: Record<HealthStatus, string> = {
  healthy: 'Healthy',
  stale: 'Stale',
  degraded: 'Degraded',
  unavailable: 'Unavailable',
  misconfigured: 'Not configured',
  unauthorized: 'Unauthorized',
  disabled: 'Disabled',
}

export const CATEGORY_LABEL: Record<string, string> = {
  'weather.precipitation': 'Precipitation',
  'weather.wind': 'Wind',
  'weather.visibility': 'Visibility',
  'weather.temperature': 'Temperature',
  'weather.alert': 'Weather warning',
  'weather.station_report': 'Station report (unitless)',
  'weather.model_report': 'Model report (unitless)',
  'radar.reflectivity': 'Radar echo',
  'traffic.flow': 'Traffic flow',
  'traffic.corridor_eta': 'Corridor travel time',
}

export function distance(metres: number | null | undefined): string {
  if (metres === null || metres === undefined) return '—'
  return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(1)} km`
}

export function age(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'age unknown'
  if (seconds < 0) return 'ahead'
  if (seconds < 90) return `${seconds}s old`
  const minutes = Math.round(seconds / 60)
  return minutes < 90 ? `${minutes} min old` : `${Math.round(minutes / 60)} h old`
}

const num = (v: unknown, digits = 0) => (typeof v === 'number' ? v.toFixed(digits) : null)

/** One line per observation, using only what the value actually contains. */
export function describe(o: Observation): string {
  const v = o.value
  switch (o.category) {
    case 'weather.precipitation':
      if (v.reported_as === 'band') return `${v.band} (reported as a band${v.condition ? `: ${v.condition}` : ''})`
      return `${num(v.precipitation_mm_h, 1) ?? '—'} mm/h → ${v.band ?? 'unbanded'}`
    case 'weather.wind':
      return v.gust_kmh === null || v.gust_kmh === undefined
        ? `sustained ${num(v.speed_kmh) ?? '—'} km/h, no gust reported`
        : `gusts ${num(v.gust_kmh)} km/h`
    case 'weather.visibility':
      return `${distance(v.visibility_m as number)} → ${v.band ?? 'unbanded'}`
    case 'weather.temperature':
      return `${num(v.temperature_c, 1)} °C`
    case 'weather.alert': {
      const band = typeof v.band === 'string' ? v.band : 'unbanded'
      const ends = typeof v.ends_at === 'string' ? ` · ends ${clockTime(v.ends_at)}` : ''
      return `${band} — ${String(v.headline ?? v.event ?? 'alert')}${ends}`
    }
    case 'weather.station_report':
    case 'weather.model_report':
      return `reported verbatim, units undocumented: ${JSON.stringify(v.reported)}`
    case 'radar.reflectivity': {
      const range = v.dbz_min === v.dbz_max ? `${v.dbz_min}` : `${v.dbz_min}–${v.dbz_max}`
      return `${range} dBZ → ${v.band} (derived) · ${String(v.note ?? '')}`
    }
    case 'traffic.flow':
      if (v.congestion_index === null || v.congestion_index === undefined)
        return `${num(v.current_speed_kmh) ?? '—'} km/h, no free-flow reference — not banded`
      return `${num(v.current_speed_kmh)} / ${num(v.free_flow_speed_kmh)} km/h free-flow → ${v.band}`
    case 'traffic.corridor_eta':
      return `${num(v.travel_time_s)} s over ${distance(v.length_m as number)}` +
        (v.reference_travel_time_s ? ` (provider optimal ${num(v.reference_travel_time_s)} s — not free-flow)` : '')
    default:
      return JSON.stringify(v)
  }
}

/**
 * Where an incident starts and ends, as the source names them. For TomTom
 * these are location names (usually cross streets), not the road the
 * incident is on, so they are always phrased as "from X to Y".
 */
export function incidentSpan(attributes: Record<string, unknown>): string {
  const from = typeof attributes.from === 'string' ? attributes.from : null
  const to = typeof attributes.to === 'string' ? attributes.to : null
  if (from && to) return `from ${from} to ${to}`
  if (from) return `starting at ${from}`
  return ''
}

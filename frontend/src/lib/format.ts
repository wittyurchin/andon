import type { EvidenceKind, Freshness, Severity, Trend } from '../types'

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'unknown'
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000)
  if (seconds < 0) return 'just now'
  if (seconds < 45) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours}h ${rest}m ago` : `${hours}h ago`
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '--:--:--'
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function shortClock(iso: string | null | undefined): string {
  if (!iso) return '--:--'
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function countdown(ms: number): string {
  if (ms <= 0) return 'now'
  const total = Math.ceil(ms / 1000)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return minutes > 0 ? `${minutes}m ${String(seconds).padStart(2, '0')}s` : `${seconds}s`
}

export const TREND_ARROW: Record<Trend, string> = {
  worsening: '↑',
  improving: '↓',
  steady: '→',
  unknown: '·',
}

export const TREND_LABEL: Record<Trend, string> = {
  worsening: 'Worsening',
  improving: 'Improving',
  steady: 'Steady',
  unknown: 'No trend yet',
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  none: 'Clear',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  severe: 'Severe',
}

export const FRESHNESS_LABEL: Record<Freshness, string> = {
  fresh: 'Fresh',
  recent: 'Recent',
  aging: 'Ageing',
  stale: 'Stale',
  unknown: 'Unknown age',
}

export const KIND_LABEL: Record<EvidenceKind, string> = {
  observed: 'Observed',
  forecast: 'Forecast',
  inferred: 'Inferred',
}

export function formatFacetValue(value: number | string | null, unit: string | null): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  // Distances keep one decimal so "0.8 km" and "2.0 km" line up in a column.
  if (unit === 'km') return `${value.toFixed(1)} km`
  if (unit === 'm' && value >= 1000) return `${(value / 1000).toFixed(1)} km`
  const rounded = Number.isInteger(value) ? value : Math.round(value * 10) / 10
  if (unit === '%') return `${rounded}%`
  return unit ? `${rounded} ${unit}` : String(rounded)
}

export function titleCase(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

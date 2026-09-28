import { useState } from 'react'
import { HEALTH_LABEL, age, describe, distance } from '../lib/evidence'
import { relativeTime } from '../lib/format'
import { COVERAGE_ICON, COVERAGE_LABEL, coverageFor, pricingFor } from '../lib/sourceMeta'
import type { EvidenceBundle, Observation, SourceHealthRecord } from '../types'
import { FreshnessMeter } from './FreshnessMeter'
import { SourceDetailPanel } from './SourceDetailPanel'

const KIND_ORDER = ['weather', 'radar', 'traffic', 'incidents']
const KIND_LABEL: Record<string, string> = {
  weather: 'Weather',
  radar: 'Radar',
  traffic: 'Traffic',
  incidents: 'Incidents',
  roads: 'Road network',
}

export const ROAD_NETWORK_ID = '__access_graph__'

/** One card per source. Click a card for everything that source has told us. */
export function SourceCards({ evidence, now }: { evidence: EvidenceBundle; now: number }) {
  const [selected, setSelected] = useState<string | null>(null)
  const sources = [...evidence.source_health].sort(
    (a, b) => KIND_ORDER.indexOf(a.source_kind) - KIND_ORDER.indexOf(b.source_kind) || a.source_id.localeCompare(b.source_id),
  )
  const graph = evidence.access_graph

  return (
    <section className="panel">
      <header className="panel__head">
        <h2 className="panel__title">Sources</h2>
        <span className="panel__meta">Click a source to see everything it has reported</span>
      </header>

      <div className="source-cards">
        {sources.map((h) => (
          <SourceCard key={h.source_id} h={h} evidence={evidence} now={now} onOpen={() => setSelected(h.source_id)} />
        ))}
        {graph && (
          <button type="button" className="source-card" onClick={() => setSelected(ROAD_NETWORK_ID)}>
            <span className="source-card__kind">{KIND_LABEL.roads}</span>
            <span className="source-card__name">
              {graph.source === 'osm' ? 'OpenStreetMap' : graph.source === 'mock' ? 'Simulated roads' : graph.source === 'radial' ? 'Radial approximation' : 'Configured points'}
            </span>
            <span className={`health health--${graph.source === 'radial' ? 'degraded' : 'healthy'}`}>
              {graph.source === 'radial' ? 'Approximation' : 'Built'}
            </span>
            <span className="source-card__headline">{graph.approaches.length} access corridors</span>
            <span className="source-card__meta">built {relativeTime(graph.built_at, now)}</span>
          </button>
        )}
      </div>

      {selected && (
        <SourceDetailPanel
          restaurantId={evidence.restaurant.id}
          sourceId={selected}
          evidence={evidence}
          now={now}
          onClose={() => setSelected(null)}
        />
      )}
    </section>
  )
}

function SourceCard({ h, evidence, now, onOpen }: {
  h: SourceHealthRecord
  evidence: EvidenceBundle
  now: number
  onOpen: () => void
}) {
  const isWeather = h.source_kind === 'weather'
  const current = evidence.observations.filter((o) => o.source_id === h.source_id)
  const forecasts = evidence.forecasts.filter((o) => o.source_id === h.source_id)
  const incidents = evidence.incidents.filter((i) => i.source_id === h.source_id && i.status === 'active')
  const lead =
    current.find((o) => o.category === 'weather.precipitation' || (o.category === 'radar.reflectivity' && o.subject_id === 'at_site')) ??
    current[0] ??
    forecasts.find((o) => o.valid_at === o.observed_at && o.category === 'weather.precipitation') ??
    forecasts[0]

  let headline = 'No data'
  if (h.source_kind === 'incidents' && h.status !== 'disabled' && h.status !== 'misconfigured') {
    headline = `${incidents.length} active incident${incidents.length === 1 ? '' : 's'}`
  } else if (lead) {
    headline = describe(lead)
  } else if (!['healthy', 'stale', 'degraded'].includes(h.status)) {
    headline = (h.error ?? (h.details.reason as string | undefined) ?? HEALTH_LABEL[h.status]).slice(0, 110)
  }
  const noData = !lead && h.source_kind !== 'incidents'
  const fullReason = h.error ?? (h.details.reason as string | undefined) ?? HEALTH_LABEL[h.status]

  const reading = isWeather ? readingBadge(h, lead) : null
  const pricing = isWeather ? pricingFor(h.source_id, h.licence_note) : null
  const coverage = isWeather ? coverageFor(h.source_id) : null
  const locationLabel = lead?.value.location_label as string | undefined

  return (
    <button type="button" className={`source-card source-card--${h.status}`} onClick={onOpen}>
      <span className="source-card__kind">
        {KIND_LABEL[h.source_kind] ?? h.source_kind}
        {h.source_type === 'mock' && <span className="tag tag--mock">mock</span>}
      </span>
      <span className="source-card__name">{h.source_name}</span>
      <span className="source-card__badges">
        <span className={`health health--${h.status}`}>{HEALTH_LABEL[h.status]}</span>
        {noData && (
          <span className="info-icon tip" title={`No data — ${fullReason}`} aria-label={`No data — ${fullReason}`}>
            ⓘ
          </span>
        )}
        {reading && (
          <span className={`tag tag--reading tag--reading-${reading.cls} tip`} title={reading.title}>
            {reading.label}
          </span>
        )}
        {pricing && <span className="tag tag--pricing tip" title={pricing.note}>{pricing.label}</span>}
      </span>
      <span className="source-card__headline">{headline}</span>
      {isWeather && (locationLabel || lead?.spatial.distance_m != null) && (
        <span className="source-card__location tip" title="Distance and location of the measurement point">
          📍 {locationLabel ?? 'Location unknown'}
          {lead?.spatial.distance_m != null && ` · ${distance(lead.spatial.distance_m)}${lead.spatial.bearing ? ` ${lead.spatial.bearing}` : ''}`}
        </span>
      )}
      {coverage && (
        <span className="source-card__coverage">
          {(Object.keys(coverage) as (keyof typeof coverage)[]).map((key) => (
            <span
              key={key}
              className={`coverage-icon coverage-icon--${coverage[key].coverage} tip`}
              title={`${COVERAGE_LABEL[key]}: ${coverage[key].note}`}
            >
              {COVERAGE_ICON[key]}
            </span>
          ))}
        </span>
      )}
      <span className="source-card__meta">
        {h.last_success_at ? `last success ${relativeTime(h.last_success_at, now)}` : 'never succeeded'}
        {isWeather && h.response_age_seconds !== null ? (
          <FreshnessMeter
            grade={h.freshness_grade}
            ageLabel={`data ${age(h.response_age_seconds)}`}
            staleAfterSeconds={h.details.stale_after_seconds as number | undefined}
          />
        ) : (
          h.response_age_seconds !== null && ` · data ${age(h.response_age_seconds)}`
        )}
        {current.length + forecasts.length > 0 && ` · ${current.length + forecasts.length} readings`}
      </span>
    </button>
  )
}

const READING_TITLE: Record<string, string> = {
  observation: 'Real — measured or reported by the source, not modelled.',
  model: "A model's estimate of current conditions, not a measurement.",
  prediction: "A model's forecast for a future time, not a measurement.",
  simulated: 'Simulated data — not a real provider.',
}

/** Real / Model / Prediction / Simulated, from the card's lead observation. */
function readingBadge(
  h: SourceHealthRecord,
  lead: Observation | undefined,
): { label: string; cls: string; title: string } | null {
  if (h.source_type === 'mock') return { label: 'Simulated', cls: 'simulated', title: READING_TITLE.simulated }
  if (!lead) return null
  if (lead.kind === 'observation') return { label: 'Real', cls: 'observation', title: READING_TITLE.observation }
  if (lead.valid_at && lead.valid_at !== lead.observed_at)
    return { label: 'Prediction', cls: 'prediction', title: READING_TITLE.prediction }
  return { label: 'Model', cls: 'model', title: READING_TITLE.model }
}

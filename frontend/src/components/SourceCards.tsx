import { useState } from 'react'
import { age, describe, distance } from '../lib/evidence'
import { relativeTime } from '../lib/format'
import { COVERAGE_LABEL, COVERAGE_SHORT, coverageFor, pricingFor } from '../lib/sourceMeta'
import type { EvidenceBundle, Observation, SourceHealthRecord } from '../types'
import { FreshnessMeter } from './FreshnessMeter'
import { HEALTH_WORD, Lamp, Mark, markFor } from './marks'
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
  const weatherSources = sources.filter((h) => h.source_kind === 'weather')
  const otherSources = sources.filter((h) => h.source_kind !== 'weather')
  const graph = evidence.access_graph
  const working = sources.filter((h) => ['healthy', 'stale', 'degraded'].includes(h.status)).length

  return (
    <section className="panel panel--channels">
      <header className="panel__head">
        <h2 className="panel__title">Sources</h2>
        <span className="panel__meta">
          {working} of {sources.length} reporting · click a source to see everything it has told us
        </span>
      </header>

      {weatherSources.length > 0 && (
        <div className="channel-group">
          <p className="channel-group__label">Weather</p>
          <div className="source-cards source-cards--row">
            {weatherSources.map((h) => (
              <SourceCard key={h.source_id} h={h} evidence={evidence} now={now} onOpen={() => setSelected(h.source_id)} />
            ))}
          </div>
        </div>
      )}

      <div className="channel-group">
        <p className="channel-group__label">Radar, roads and incidents</p>
        <div className="source-cards">
          {otherSources.map((h) => (
            <SourceCard key={h.source_id} h={h} evidence={evidence} now={now} onOpen={() => setSelected(h.source_id)} />
          ))}
          {graph && (
            <button type="button" className="source-card source-card--healthy" onClick={() => setSelected(ROAD_NETWORK_ID)}>
              <span className="source-card__top">
                <span className="source-card__kind">{KIND_LABEL.roads}</span>
                <span className={`lamp lamp--${graph.source === 'radial' ? 'dim' : 'lit'}`}>
                  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                    {graph.source === 'radial' ? (
                      <>
                        <circle cx="5" cy="5" r="3.6" className="lamp__ring" />
                        <path d="M5 1.4 A3.6 3.6 0 0 1 5 8.6 Z" />
                      </>
                    ) : (
                      <circle cx="5" cy="5" r="4" />
                    )}
                  </svg>
                  {graph.source === 'radial' ? 'Approximation' : 'Built'}
                </span>
              </span>
              <span className="source-card__name">
                {graph.source === 'osm' ? 'OpenStreetMap' : graph.source === 'mock' ? 'Simulated roads' : graph.source === 'radial' ? 'Radial approximation' : 'Configured points'}
              </span>
              <span className="source-card__headline">{graph.approaches.length} access roads mapped</span>
              <span className="source-card__meta">built {relativeTime(graph.built_at, now)}</span>
            </button>
          )}
        </div>
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
  // Verbatim "report" rows are bookkeeping (undocumented units, which state
  // was checked), not a reading to lead with.
  const isReport = (o: Observation) => o.category.endsWith('_report')
  const alerts = forecasts.filter((o) => o.category === 'weather.alert')
  const alertSource = coverageFor(h.source_id)?.alerts.coverage === 'covered' && !current.length && !forecasts.some((o) => o.category === 'weather.precipitation')
  const lead: Observation | undefined =
    current.find((o) => o.category === 'weather.precipitation' || (o.category === 'radar.reflectivity' && o.subject_id === 'at_site')) ??
    current.find((o) => !isReport(o)) ??
    forecasts.find((o) => o.valid_at === o.observed_at && o.category === 'weather.precipitation') ??
    alerts[0] ??
    forecasts.find((o) => !isReport(o)) ??
    current[0] ??
    forecasts[0]
  const checkedState = forecasts.find(isReport)?.value.reported as Record<string, unknown> | undefined

  let headline = 'No data'
  if (h.source_kind === 'incidents' && h.status !== 'disabled' && h.status !== 'misconfigured') {
    headline = `${incidents.length} active incident${incidents.length === 1 ? '' : 's'}`
  } else if (alertSource && ['healthy', 'stale', 'degraded'].includes(h.status)) {
    const state = typeof checkedState?.state_checked === 'string' ? ` for ${checkedState.state_checked}` : ''
    headline = alerts.length
      ? `${alerts.length} active alert${alerts.length === 1 ? '' : 's'}${state}: ${describe(alerts[0])}`
      : `No active official alert${state}`
  } else if (lead) {
    headline = describe(lead)
  } else if (!['healthy', 'stale', 'degraded'].includes(h.status)) {
    const reason = h.error ?? (h.details.reason as string | undefined) ?? HEALTH_WORD[h.status]
    // Cut at a word boundary; the full reason is one click away in the drawer.
    headline = reason.length > 110 ? `${reason.slice(0, 110).replace(/\s+\S*$/, '')}…` : reason
  }
  const noData = !lead && h.source_kind !== 'incidents'
  const fullReason = h.error ?? (h.details.reason as string | undefined) ?? HEALTH_WORD[h.status]

  const mark = alertSource
    ? alerts.length ? markFor(alerts[0]) : null
    : lead && !isReport(lead) ? markFor(lead) : h.source_type === 'mock' ? 'simulated' : null
  const pricing = isWeather ? pricingFor(h.source_id, h.licence_note) : null
  const coverage = isWeather ? coverageFor(h.source_id) : null
  const locationLabel = lead?.value.location_label as string | undefined
  const down = !['healthy', 'stale', 'degraded'].includes(h.status)

  return (
    <button type="button" className={`source-card source-card--${h.status}`} onClick={onOpen}>
      <span className="source-card__top">
        <span className="source-card__kind">
          {KIND_LABEL[h.source_kind] ?? h.source_kind}
          {h.source_type === 'mock' && <span className="tag tag--mock">mock</span>}
        </span>
        <Lamp status={h.status} />
      </span>
      <span className="source-card__name">{h.source_name}</span>

      {(mark || pricing || noData) && (
        <span className="source-card__badges">
          {mark && !down && <Mark kind={mark} />}
          {pricing && (
            <span className="tag tag--pricing tip" title={pricing.note}>
              {pricing.label}
            </span>
          )}
          {noData && !down && (
            <span className="info-icon tip" title={`No data: ${fullReason}`} aria-label={`No data: ${fullReason}`}>
              no data
            </span>
          )}
        </span>
      )}

      <span className={`source-card__headline ${down ? 'is-reason' : ''}`}>{headline}</span>

      {isWeather && (locationLabel || lead?.spatial.distance_m != null) && (
        <span className="source-card__location tip" title="Where this reading was taken, relative to the kitchen">
          <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true">
            <circle cx="5" cy="5" r="3.5" fill="none" strokeWidth="1.4" stroke="currentColor" />
            <circle cx="5" cy="5" r="1" fill="currentColor" />
          </svg>
          {locationLabel ?? 'Location unknown'}
          {lead?.spatial.distance_m != null && ` · ${distance(lead.spatial.distance_m)}${lead.spatial.bearing ? ` ${lead.spatial.bearing}` : ''}`}
        </span>
      )}

      {coverage && (
        <span className="coverage-strip" aria-label="What this source can report">
          {(Object.keys(coverage) as (keyof typeof coverage)[]).map((key) => (
            <span
              key={key}
              className={`coverage-chip coverage-chip--${coverage[key].coverage} tip`}
              title={`${COVERAGE_LABEL[key]}: ${coverage[key].note}`}
            >
              {COVERAGE_SHORT[key]}
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
          h.response_age_seconds !== null && <span>data {age(h.response_age_seconds)}</span>
        )}
        {current.length + forecasts.length > 0 && <span>{current.length + forecasts.length} readings</span>}
      </span>
    </button>
  )
}

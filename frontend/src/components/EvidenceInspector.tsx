import { useMemo, useState } from 'react'
import { APPLICABILITY_LABEL, CATEGORY_LABEL, age, describe, distance } from '../lib/evidence'
import { clockTime } from '../lib/format'
import type { EvidenceBundle, Observation } from '../types'

export type InspectorFilter = 'all' | 'weather' | 'radar' | 'traffic' | 'forecast'

const FILTERS: { key: InspectorFilter; label: string }[] = [
  { key: 'all', label: 'All current' },
  { key: 'weather', label: 'Weather' },
  { key: 'radar', label: 'Radar' },
  { key: 'traffic', label: 'Traffic' },
  { key: 'forecast', label: 'Forecasts' },
]

function matches(o: Observation, filter: InspectorFilter): boolean {
  if (filter === 'all') return o.kind === 'observation'
  if (filter === 'forecast') return o.kind === 'forecast'
  return o.category.startsWith(filter === 'weather' ? 'weather.' : `${filter}.`) && o.kind === 'observation'
}

/**
 * The underlying evidence, exactly as stored: source, kind, time, distance,
 * relevance, confidence, staleness. Conflicts are shown first and open.
 */
export function EvidenceInspector({
  evidence,
  filter,
  onFilter,
}: {
  evidence: EvidenceBundle
  filter: InspectorFilter
  onFilter: (f: InspectorFilter) => void
}) {
  const [showAllForecasts, setShowAllForecasts] = useState(false)
  const rows = useMemo(() => {
    const pool = [...evidence.observations, ...evidence.forecasts].filter((o) => matches(o, filter))
    const sorted = pool.sort((a, b) =>
      a.category === b.category ? b.spatial.relevance - a.spatial.relevance : a.category.localeCompare(b.category),
    )
    return filter === 'forecast' && !showAllForecasts ? sorted.slice(0, 12) : sorted
  }, [evidence, filter, showAllForecasts])

  const byId = useMemo(
    () => new Map([...evidence.observations, ...evidence.forecasts].map((o) => [o.id, o])),
    [evidence],
  )

  return (
    <section className="panel panel--evidence" id="evidence">
      <header className="panel__head">
        <h2 className="panel__title">Evidence</h2>
        <span className="panel__meta">
          {evidence.observations.length} current observations · {evidence.forecasts.length} forecast values ·
          {' '}refreshed {clockTime(evidence.generated_at)}
        </span>
      </header>

      {evidence.conflicts.map((conflict) => (
        <div key={conflict.category + conflict.subject} className="conflict">
          <p className="conflict__title">
            <strong>Sources disagree</strong> — {conflict.subject} ({conflict.spread} band
            {conflict.spread === 1 ? '' : 's'} apart). Both are kept; neither is averaged away.
          </p>
          <ul className="conflict__members">
            {conflict.members.map((m) => {
              const o = byId.get(m.observation_id)
              return (
                <li key={m.observation_id}>
                  <span className={`kind kind--${m.kind === 'observation' ? 'observed' : 'forecast'}`}>{m.kind}</span>
                  <span className="conflict__source">{m.source_name}</span>
                  <span className={`sev-text-${m.band ?? 'none'} conflict__band`}>{m.band}</span>
                  <span className="conflict__meta">
                    {distance(m.distance_m)} away · relevance {Math.round(m.relevance * 100)}%
                    {o?.raw_reference ? ` · “${o.raw_reference}”` : ''}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      ))}

      <div className="tabs" role="tablist">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            role="tab"
            aria-selected={filter === f.key}
            className={`tab ${filter === f.key ? 'tab--active' : ''}`}
            onClick={() => onFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <p className="empty">No evidence of this kind right now.</p>
      ) : (
        <table className="evidence-table">
          <thead>
            <tr>
              <th>What</th>
              <th>Value</th>
              <th>Source</th>
              <th>When</th>
              <th>Where</th>
              <th>Conf.</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id} className={o.stale ? 'is-stale' : ''}>
                <td>
                  <span className={`kind kind--${o.kind === 'observation' ? 'observed' : 'forecast'}`}>{o.kind}</span>
                  <span className="evidence-table__cat">{CATEGORY_LABEL[o.category] ?? o.category}</span>
                  {o.subject_id && <span className="evidence-table__subject">{o.subject_id.split(':').pop()}</span>}
                </td>
                <td>
                  {describe(o)}
                  {typeof o.value.match_warning === 'string' && (
                    <span className="evidence-table__warn">{o.value.match_warning}</span>
                  )}
                  {o.raw_reference && <span className="evidence-table__raw">“{o.raw_reference}”</span>}
                </td>
                <td>
                  {o.source_name}
                  {o.source_type === 'mock' && <span className="tag tag--mock">mock</span>}
                </td>
                <td>
                  {o.kind === 'forecast' && o.valid_at ? `for ${clockTime(o.valid_at)}` : clockTime(o.observed_at)}
                  <span className={`evidence-table__age ${o.stale ? 'freshness--stale' : ''}`}>
                    {o.stale ? 'STALE · ' : ''}
                    {age(o.freshness_seconds)}
                    {o.observed_at_basis === 'received' ? ' (receipt time — source gave none)' : ''}
                  </span>
                </td>
                <td title={o.spatial.basis}>
                  {distance(o.spatial.distance_m)}
                  {o.spatial.bearing ? ` ${o.spatial.bearing}` : ''}
                  <span className="evidence-table__rel">
                    {APPLICABILITY_LABEL[o.spatial.applicability]} · relevance {Math.round(o.spatial.relevance * 100)}%
                  </span>
                </td>
                <td>{Math.round(o.confidence * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {filter === 'forecast' && evidence.forecasts.length > 12 && (
        <button type="button" className="link" onClick={() => setShowAllForecasts((v) => !v)}>
          {showAllForecasts ? 'Show fewer' : `Show all ${evidence.forecasts.length} forecast values`}
        </button>
      )}
    </section>
  )
}

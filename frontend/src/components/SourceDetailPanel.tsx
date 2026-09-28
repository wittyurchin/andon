import { Fragment, useEffect, useState } from 'react'
import { fetchSourceDetail } from '../api'
import { ROAD_NETWORK_INFO, SOURCE_INFO, type SourceInfoEntry } from '../data/sourceInfo'
import { APPLICABILITY_LABEL, CATEGORY_LABEL, HEALTH_LABEL, age, describe, distance } from '../lib/evidence'
import { clockTime, relativeTime } from '../lib/format'
import type { EvidenceBundle, Observation, SourceDetail } from '../types'
import { ROAD_NETWORK_ID } from './SourceCards'

type Tab = 'details' | 'info'

/** Everything one source has told us, with full provenance. */
export function SourceDetailPanel({ restaurantId, sourceId, evidence, now, onClose }: {
  restaurantId: string
  sourceId: string
  evidence: EvidenceBundle
  now: number
  onClose: () => void
}) {
  const [detail, setDetail] = useState<SourceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('details')

  useEffect(() => {
    if (sourceId === ROAD_NETWORK_ID) return
    const controller = new AbortController()
    setDetail(null)
    setError(null)
    fetchSourceDetail(restaurantId, sourceId, controller.signal)
      .then(setDetail)
      .catch((err) => (err as Error).name !== 'AbortError' && setError((err as Error).message))
    return () => controller.abort()
  }, [restaurantId, sourceId])

  useEffect(() => {
    setTab('details')
  }, [sourceId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const info = sourceId === ROAD_NETWORK_ID ? ROAD_NETWORK_INFO : SOURCE_INFO[sourceId]
  const isRoadNetwork = sourceId === ROAD_NETWORK_ID
  const graph = isRoadNetwork ? evidence.access_graph : undefined
  const health = detail?.health
  const eyebrow = isRoadNetwork ? 'Road network' : `${detail?.source?.kind ?? health?.source_kind ?? ''} source`
  const title = isRoadNetwork
    ? `Access graph (${graph?.source ?? '…'})`
    : detail?.source?.name ?? health?.source_name ?? '…'

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="drawer__close" onClick={onClose} aria-label="Close">×</button>

        <header className="drawer__head">
          <p className="eyebrow">{eyebrow}</p>
          <h2 className="drawer__title">{title}</h2>
          {!isRoadNetwork && health && <span className={`health health--${health.status}`}>{HEALTH_LABEL[health.status]}</span>}
        </header>

        <div className="drawer__tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'details'}
            className={`drawer__tab ${tab === 'details' ? 'drawer__tab--active' : ''}`}
            onClick={() => setTab('details')}
          >
            Details
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'info'}
            className={`drawer__tab ${tab === 'info' ? 'drawer__tab--active' : ''}`}
            onClick={() => setTab('info')}
          >
            Info
          </button>
        </div>
        {tab === 'info' ? (
          <SourceInfoTab info={info} />
        ) : isRoadNetwork ? (
          <RoadNetwork evidence={evidence} now={now} />
        ) : error ? (
          <p className="alert">{error}</p>
        ) : !detail ? (
          <p className="empty">Loading…</p>
        ) : (
          <Detail detail={detail} now={now} />
        )}
      </aside>
    </div>
  )
}

function SourceInfoTab({ info }: { info: SourceInfoEntry | undefined }) {
  if (!info) {
    return (
      <section className="drawer__section">
        <p className="empty">No reference write-up for this source yet.</p>
      </section>
    )
  }
  return (
    <section className="drawer__section drawer__info">
      <p className="drawer__info-summary">{info.summary}</p>

      <h3 className="report__subtitle">How it gets here</h3>
      <p>{info.how}</p>

      {info.reports.length > 0 && (
        <>
          <h3 className="report__subtitle">What it reports</h3>
          <ul>
            {info.reports.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      )}

      {info.limits.length > 0 && (
        <>
          <h3 className="report__subtitle">What it won’t do</h3>
          <ul>
            {info.limits.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      )}

      {info.confidence && (
        <>
          <h3 className="report__subtitle">Confidence</h3>
          <p>{info.confidence}</p>
        </>
      )}
    </section>
  )
}

function Detail({ detail, now }: { detail: SourceDetail; now: number }) {
  const { source, health } = detail
  const reason = health?.error ?? (health?.details.reason as string | undefined)
  return (
    <>
      <section className="drawer__section">
        <h3 className="report__subtitle">Status</h3>
        <dl className="kv">
          <dt>Source id</dt><dd>{source?.id ?? health?.source_id}</dd>
          <dt>Type</dt><dd>{source?.mode === 'mock' ? 'Mock — simulated, not real' : 'Live external provider'}</dd>
          <dt>Last checked</dt><dd>{health ? `${clockTime(health.checked_at)} (${relativeTime(health.checked_at, now)})` : '—'}</dd>
          <dt>Last success</dt><dd>{health?.last_success_at ? relativeTime(health.last_success_at, now) : 'never'}</dd>
          <dt>Last failure</dt><dd>{health?.last_failure_at ? relativeTime(health.last_failure_at, now) : '—'}</dd>
          <dt>Consecutive failures</dt><dd>{health?.consecutive_failures ?? '—'}</dd>
          <dt>Latency</dt><dd>{health?.latency_ms != null ? `${health.latency_ms} ms` : '—'}</dd>
          <dt>Data age</dt><dd>{health?.response_age_seconds != null ? age(health.response_age_seconds) : '—'}</dd>
          <dt>Stale after</dt><dd>{health?.details.stale_after_seconds ? `${Math.round(Number(health.details.stale_after_seconds) / 60)} min (engineering default)` : '—'}</dd>
          {reason && (<><dt>Reason</dt><dd className="kv__warn">{reason}</dd></>)}
          {health?.details.requires != null && (<><dt>Needs</dt><dd>{String(health.details.requires)}</dd></>)}
          {Array.isArray(health?.details.covered_categories) && (
            <><dt>Can report</dt><dd>{(health!.details.covered_categories as string[]).join(', ')}</dd></>
          )}
        </dl>
      </section>

      <section className="drawer__section">
        <h3 className="report__subtitle">Provenance</h3>
        <dl className="kv">
          <dt>Attribution</dt><dd>{source?.attribution ?? '—'}</dd>
          {source?.licence_note && (<><dt>Licence</dt><dd className="kv__warn">{source.licence_note}</dd></>)}
          <dt>Documentation</dt>
          <dd>{source?.docs_url ? <a href={source.docs_url} target="_blank" rel="noreferrer">{source.docs_url}</a> : '—'}</dd>
          <dt>Verification</dt><dd>{detail.verification_doc}</dd>
        </dl>
      </section>

      <ObservationTable title="Current readings" rows={detail.observations} empty="No current readings from this source." />
      {detail.forecasts.length > 0 && <ObservationTable title="Forecast" rows={detail.forecasts} />}

      {detail.incidents.length > 0 && (
        <section className="drawer__section">
          <h3 className="report__subtitle">Incidents ({detail.incidents.length})</h3>
          <table className="evidence-table">
            <thead><tr><th>Incident</th><th>Status</th><th>Seen</th><th>Where</th><th>Conf.</th></tr></thead>
            <tbody>
              {detail.incidents.map((i) => (
                <tr key={i.id}>
                  <td>
                    {i.incident_type.replace(/_/g, ' ')}
                    <span className="evidence-table__cat">{i.description}</span>
                    <span className="evidence-table__subject">record {i.source_record_id ?? 'none (no id from source)'}</span>
                  </td>
                  <td>{i.status}{i.cleared_at ? ` ${relativeTime(i.cleared_at, now)}` : ''}</td>
                  <td>
                    first {clockTime(i.first_seen)}
                    <span className="evidence-table__age">last {relativeTime(i.last_seen, now)}</span>
                  </td>
                  <td title={i.spatial.basis}>
                    {distance(i.spatial.distance_m)} {i.spatial.bearing ?? ''}
                    <span className="evidence-table__rel">{i.spatial.on_approach ? 'on an access corridor' : 'off corridor'}</span>
                  </td>
                  <td>{Math.round(i.confidence * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <ObservationTable
        title={`History — last ${detail.history_minutes} min (${detail.history.length})`}
        rows={detail.history}
        empty="No stored history yet."
      />
    </>
  )
}

function ObservationTable({ title, rows, empty }: { title: string; rows: Observation[]; empty?: string }) {
  return (
    <section className="drawer__section">
      <h3 className="report__subtitle">{title}</h3>
      {rows.length === 0 ? (
        <p className="empty">{empty}</p>
      ) : (
        <table className="evidence-table">
          <thead><tr><th>What</th><th>Value</th><th>When</th><th>Where</th><th>Conf.</th></tr></thead>
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
                  {typeof o.value.match_warning === 'string' && <span className="evidence-table__warn">{o.value.match_warning}</span>}
                  {o.raw_reference && <span className="evidence-table__raw">“{o.raw_reference}”</span>}
                </td>
                <td>
                  {o.kind === 'forecast' && o.valid_at ? `for ${clockTime(o.valid_at)}` : clockTime(o.observed_at)}
                  <span className={`evidence-table__age ${o.stale ? 'freshness--stale' : ''}`}>
                    {o.stale ? 'STALE · ' : ''}{age(o.freshness_seconds)}
                    {o.observed_at_basis === 'received' ? ' (receipt time)' : ''}
                  </span>
                </td>
                <td title={o.spatial.basis}>
                  {distance(o.spatial.distance_m)}{o.spatial.bearing ? ` ${o.spatial.bearing}` : ''}
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
    </section>
  )
}

function RoadNetwork({ evidence, now }: { evidence: EvidenceBundle; now: number }) {
  const graph = evidence.access_graph!
  return (
    <>
      <section className="drawer__section">
        <dl className="kv">
          <dt>Built</dt><dd>{clockTime(graph.built_at)} ({relativeTime(graph.built_at, now)})</dd>
          <dt>Radius</dt><dd>{graph.radius_m} m</dd>
          <dt>How derived</dt><dd>{graph.derivation}</dd>
          {graph.attribution && (<><dt>Attribution</dt><dd>{graph.attribution}</dd></>)}
          {graph.notes.map((n) => (<Fragment key={n}><dt>Note</dt><dd className="kv__warn">{n}</dd></Fragment>))}
        </dl>
      </section>
      <section className="drawer__section">
        <h3 className="report__subtitle">Corridors ({graph.approaches.length})</h3>
        <table className="evidence-table">
          <thead><tr><th>Corridor</th><th>Class</th><th>Nearest</th><th>Length</th><th>Segments</th></tr></thead>
          <tbody>
            {graph.approaches.map((a) => (
              <tr key={a.id}>
                <td>
                  {a.label}
                  <span className="evidence-table__subject">{a.derivation}</span>
                  <span className="evidence-table__rel">probe {a.probe.lat.toFixed(5)}, {a.probe.lon.toFixed(5)}</span>
                </td>
                <td>{a.road_class ?? '—'}</td>
                <td>{distance(a.distance_m)}</td>
                <td>{distance(a.length_m)}</td>
                <td>
                  {a.segment_ids.length}
                  <span className="evidence-table__subject">{a.segment_ids.slice(0, 3).join(', ')}{a.segment_ids.length > 3 ? '…' : ''}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  )
}

import { HEALTH_LABEL } from '../lib/evidence'
import { relativeTime } from '../lib/format'
import type { EvidenceBundle, SourceHealthRecord } from '../types'

const KIND_ORDER = ['weather', 'radar', 'traffic', 'incidents']

/**
 * The source health registry: every configured source, including the ones we
 * deliberately do not call, with the exact reason. Nothing here is hidden
 * because it is inconvenient.
 */
export function SourcesPanel({ evidence, now }: { evidence: EvidenceBundle; now: number }) {
  const records = [...evidence.source_health].sort(
    (a, b) => KIND_ORDER.indexOf(a.source_kind) - KIND_ORDER.indexOf(b.source_kind) || a.source_id.localeCompare(b.source_id),
  )
  const coverage = evidence.coverage

  return (
    <section className="panel panel--sources">
      <header className="panel__head">
        <h2 className="panel__title">Data sources</h2>
        <span className="panel__meta">
          {records.filter((r) => ['healthy', 'stale', 'degraded'].includes(r.status)).length} of {records.length} contributing
        </span>
      </header>

      <table className="sources">
        <thead>
          <tr>
            <th>Source</th>
            <th>Signal</th>
            <th>Status</th>
            <th>Last success</th>
            <th>Data age</th>
            <th>Failures</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r) => (
            <Row key={r.source_id} r={r} now={now} />
          ))}
        </tbody>
      </table>

      <div className="coverage">
        <h3 className="report__subtitle">What we can’t see</h3>
        <ul className="list list--muted">
          {coverage.weather && <li>{coverage.weather.statement}</li>}
          {coverage.traffic && coverage.traffic.approaches_unobserved.length > 0 && (
            <li>No traffic reading for: {coverage.traffic.approaches_unobserved.join('; ')}.</li>
          )}
          {coverage.traffic?.graph_is_approximation && (
            <li>Access corridors are approximations, not mapped roads.</li>
          )}
          {coverage.incidents && coverage.incidents.categories_not_covered.length > 0 && (
            <li>
              No incident source reports: {coverage.incidents.categories_not_covered.map((c) => c.replace(/_/g, ' ')).join(', ')}
              {' '}— silence on these is not an all-clear.
            </li>
          )}
          {(coverage.stale_sources ?? []).length > 0 && <li>Stale: {coverage.stale_sources!.join(', ')}.</li>}
        </ul>
      </div>

      {evidence.attributions.length > 0 && (
        <p className="attribution">Data: {evidence.attributions.join(' · ')}</p>
      )}
    </section>
  )
}

function Row({ r, now }: { r: SourceHealthRecord; now: number }) {
  const reason = r.error ?? (r.details.reason as string | undefined)
  const requires = r.details.requires as string | undefined
  return (
    <tr className={['unavailable', 'unauthorized'].includes(r.status) ? 'is-down' : ''}>
      <td>
        <span className="sources__name">{r.source_name}</span>
        {r.source_type === 'mock' && <span className="tag tag--mock">mock</span>}
        {r.details.cached === true && <span className="tag tag--cached">cached</span>}
        {reason && <span className="sources__error">{reason}</span>}
        {r.licence_note && <span className="sources__attribution">{r.licence_note}</span>}
      </td>
      <td>{r.source_kind}</td>
      <td>
        <span className={`health health--${r.status}`}>{HEALTH_LABEL[r.status]}</span>
        {requires && <span className="sources__attribution">needs {requires}</span>}
      </td>
      <td>{r.last_success_at ? relativeTime(r.last_success_at, now) : 'never'}</td>
      <td>
        {r.response_age_seconds === null ? '—' : `${Math.round(r.response_age_seconds / 60)} min`}
      </td>
      <td>{r.consecutive_failures || '—'}</td>
    </tr>
  )
}

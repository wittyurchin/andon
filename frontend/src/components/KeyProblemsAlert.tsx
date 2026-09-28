import type { EvidenceBundle } from '../types'
import { Lamp } from './marks'

/** Sources whose API key was rejected or ran out of quota. Never hidden behind a mock. */
export function KeyProblemsAlert({ evidence }: { evidence: EvidenceBundle }) {
  const failing = evidence.source_health.filter((h) => typeof h.details.key_problem === 'string')
  if (failing.length === 0) return null

  return (
    <div className="alert alert--loud" role="alert">
      <strong>
        {failing.length === 1 ? 'A data source is failing' : `${failing.length} data sources are failing`} because
        of an API key. Their data is missing from this assessment; nothing has been substituted.
      </strong>
      <ul className="alert__list">
        {failing.map((h) => (
          <li key={h.source_id}>
            <Lamp status={h.status} showLabel={false} />
            <b>{h.source_name}</b>
            {h.consecutive_failures > 1 && ` · failed ${h.consecutive_failures} times in a row`}
            <span className="alert__detail">{h.error}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

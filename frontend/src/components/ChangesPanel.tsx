import { shortClock } from '../lib/format'
import type { ChangeEvent } from '../types'

const ICON: Record<string, string> = {
  precipitation_intensified: '↑',
  precipitation_weakened: '↓',
  traffic_worsened: '↑',
  traffic_improved: '↓',
  approach_state_changed: '⇄',
  incident_appeared: '+',
  incident_cleared: '−',
  source_became_available: '●',
  source_became_unavailable: '○',
  source_health_changed: '◐',
  confidence_changed: '≈',
  situation_level_changed: '◆',
}

const WORSE = new Set([
  'precipitation_intensified',
  'traffic_worsened',
  'incident_appeared',
  'source_became_unavailable',
])
const BETTER = new Set(['precipitation_weakened', 'traffic_improved', 'incident_cleared', 'source_became_available'])

/** Deterministic changes since earlier refreshes — no LLM involved. */
export function ChangesPanel({ changes }: { changes: ChangeEvent[] }) {
  return (
    <section className="panel">
      <header className="panel__head">
        <h2 className="panel__title">What changed</h2>
        <span className="panel__meta">Detected by comparing successive evidence states</span>
      </header>
      {changes.length === 0 ? (
        <p className="empty">
          Nothing material has changed in the last two hours. (The first refresh sets the baseline.)
        </p>
      ) : (
        <ul className="changes">
          {changes.slice(0, 12).map((change) => {
            const tone = change.change_type === 'situation_level_changed'
              ? (change.magnitude ?? 0) > 0 ? 'worse' : 'better'
              : WORSE.has(change.change_type) ? 'worse' : BETTER.has(change.change_type) ? 'better' : 'neutral'
            return (
              <li key={change.id} className={`changes__item changes__item--${tone}`}>
                <span className="changes__time">{shortClock(change.detected_at)}</span>
                <span className="changes__icon" aria-hidden="true">{ICON[change.change_type] ?? '•'}</span>
                <span className="changes__text">{change.summary}</span>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

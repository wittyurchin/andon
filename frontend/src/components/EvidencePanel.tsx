import { relativeTime } from '../lib/format'
import type { Evidence, NormalizedSituation } from '../types'
import { Mark, markForKind } from './marks'

/** "Why?" — the strongest evidence behind the current assessment. */
export function EvidencePanel({
  situation,
  now,
  limit = 6,
}: {
  situation: NormalizedSituation
  now: number
  limit?: number
}) {
  // Take each signal's strongest item first, then fill the rest by weight —
  // otherwise one noisy signal (usually traffic, with a probe per approach)
  // crowds the others out of the panel entirely.
  const signals = [situation.weather, situation.traffic, situation.road_conditions]
  const leads = signals
    .map((signal) => signal.evidence[0])
    .filter((item): item is Evidence => Boolean(item))
  const rest = signals
    .flatMap((signal) => signal.evidence.slice(1))
    .sort((a, b) => b.weight - a.weight)

  const items: Evidence[] = [...leads.sort((a, b) => b.weight - a.weight), ...rest].slice(0, limit)

  return (
    <section className="panel">
      <header className="panel__head">
        <h2 className="panel__title">Why?</h2>
        <span className="panel__meta">Strongest evidence, most significant first</span>
      </header>

      {items.length === 0 ? (
        <p className="empty">No signal is currently above its normal threshold.</p>
      ) : (
        <ul className="evidence">
          {items.map((item, index) => (
            <li key={`${item.source_id}-${index}`} className="evidence__item">
              <Mark kind={markForKind(item.kind)} />
              <span className="evidence__text">{item.text}</span>
              <span className="evidence__meta">
                {item.source_name} · {relativeTime(item.observed_at, now)}
                {item.distance_km !== null && item.distance_km > 0 && (
                  <> · {item.distance_km.toFixed(1)} km away</>
                )}
                {' · '}
                {item.confidence} confidence
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

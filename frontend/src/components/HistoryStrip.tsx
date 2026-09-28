import { SEVERITY_LABEL, shortClock } from '../lib/format'
import type { SituationSnapshot } from '../types'

export function HistoryStrip({
  history,
  note,
}: {
  history: SituationSnapshot[]
  note: string | null
}) {
  const recent = history.slice(-16)

  return (
    <section className="panel panel--history">
      <header className="panel__head">
        <h2 className="panel__title">Recent history</h2>
        {note && <span className="panel__meta">{note}</span>}
      </header>

      {recent.length < 2 ? (
        <p className="empty">
          {recent.length === 0
            ? 'No snapshots recorded yet.'
            : 'One snapshot so far — the trend line builds as refreshes accumulate.'}
        </p>
      ) : (
        <ol className="history">
          {recent.map((snapshot, index) => (
            <li key={`${snapshot.at}-${index}`} className={`history__item sev-${snapshot.level}`}>
              <span className="history__bar" style={{ height: `${8 + snapshot.score * 0.42}px` }} />
              <span className="history__time">{shortClock(snapshot.at)}</span>
              <span className="history__level">{SEVERITY_LABEL[snapshot.level].toUpperCase()}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

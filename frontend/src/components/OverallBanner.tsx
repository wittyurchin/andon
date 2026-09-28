import { TREND_ARROW, TREND_LABEL } from '../lib/format'
import type { NormalizedSituation } from '../types'

export function OverallBanner({ situation }: { situation: NormalizedSituation }) {
  const { overall } = situation

  return (
    <section className={`banner sev-${overall.level}`} aria-live="polite">
      <div className="banner__main">
        <p className="eyebrow">Current situation</p>
        <h2 className="banner__level">{overall.label}</h2>
        <p className="banner__headline">{overall.headline}</p>
      </div>

      <div className="banner__meta">
        <div className="banner__score">
          <span className="banner__score-value">{overall.score}</span>
          <span className="banner__score-label">disruption score</span>
        </div>
        <div className={`banner__trend trend-${overall.trend}`}>
          <p className="banner__trend-main">
            <span aria-hidden="true">{TREND_ARROW[overall.trend]}</span>
            {TREND_LABEL[overall.trend]}
          </p>
          {overall.trend_note && <p className="banner__trend-note">{overall.trend_note}</p>}
        </div>
        <p className="banner__confidence">Confidence: {overall.confidence}</p>
      </div>

      {situation.missing_signals.length > 0 && (
        <p className="banner__warning">
          Assessed without {situation.missing_signals.map((s) => s.replace(/_/g, ' ')).join(', ')} —
          that signal is unknown, not confirmed clear.
        </p>
      )}
    </section>
  )
}

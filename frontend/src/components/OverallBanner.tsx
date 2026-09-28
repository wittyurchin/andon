import { corridorLevels } from '../lib/corridors'
import { TREND_ARROW, TREND_LABEL } from '../lib/format'
import type { EvidenceBundle, NormalizedSituation } from '../types'
import { AndonStack } from './marks'
import { SiteCompass } from './SiteCompass'

export function OverallBanner({ situation, evidence }: { situation: NormalizedSituation; evidence: EvidenceBundle }) {
  const { overall } = situation

  return (
    <section className={`verdict sev-${overall.level}`} aria-live="polite">
      <div className="verdict__read">
        <p className="eyebrow">Current situation</p>
        <div className="verdict__level-row">
          <AndonStack level={overall.level} />
          <div>
            <h2 className="verdict__level">{overall.label}</h2>
            <p className="verdict__headline">{overall.headline}</p>
          </div>
        </div>

        <dl className="verdict__stats">
          <div className="stat">
            <dt>Disruption score</dt>
            <dd>
              <span className="stat__num">{overall.score}</span>
              <span className="stat__of">/ 100</span>
              <span className="scale" aria-hidden="true">
                <span className="scale__fill" style={{ width: `${Math.max(2, Math.min(100, overall.score))}%` }} />
              </span>
            </dd>
          </div>
          <div className={`stat trend-${overall.trend}`}>
            <dt>Trend</dt>
            <dd>
              <span className="stat__arrow" aria-hidden="true">{TREND_ARROW[overall.trend]}</span>
              {TREND_LABEL[overall.trend]}
              {overall.trend_note && <span className="stat__note">{overall.trend_note}</span>}
            </dd>
          </div>
          <div className={`stat confidence--${overall.confidence}`}>
            <dt>Confidence</dt>
            <dd>
              <span className="conf" aria-hidden="true">
                {['low', 'medium', 'high'].map((c, i) => (
                  <span key={c} className={i <= ['low', 'medium', 'high'].indexOf(overall.confidence) ? 'is-on' : ''} />
                ))}
              </span>
              {overall.confidence}
            </dd>
          </div>
        </dl>

        {situation.missing_signals.length > 0 && (
          <p className="verdict__warning">
            <strong>Assessed without {situation.missing_signals.map((s) => s.replace(/_/g, ' ')).join(', ')}.</strong>{' '}
            That signal is unknown, not confirmed clear.
          </p>
        )}
      </div>

      <SiteCompass evidence={evidence} corridorLevels={corridorLevels(evidence)} />
    </section>
  )
}

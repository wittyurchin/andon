import { TREND_ARROW, TREND_LABEL } from '../lib/format'
import type { SituationReport, TrendItem } from '../types'

/** "What's changing?" — deterministic trends, plus the model's read on them. */
export function TrendPanel({
  trends,
  report,
}: {
  trends: TrendItem[]
  report: SituationReport
}) {
  return (
    <section className="panel">
      <header className="panel__head">
        <h2 className="panel__title">What's changing?</h2>
      </header>

      <ul className="trends">
        {trends.map((item) => (
          <li key={item.label} className={`trends__row trend-${item.trend}`}>
            <span className="trends__label">{item.label}</span>
            <span className="trends__arrow" aria-hidden="true">
              {TREND_ARROW[item.trend]}
            </span>
            <span className="trends__value">{TREND_LABEL[item.trend]}</span>
            {item.detail && <span className="trends__detail">{item.detail}</span>}
          </li>
        ))}
      </ul>

      {(report.worsening.length > 0 || report.improving.length > 0) && (
        <div className="trends__narrative">
          {report.worsening.length > 0 && (
            <div>
              <h4 className="report__subtitle">Getting worse</h4>
              <ul className="list">
                {report.worsening.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          {report.improving.length > 0 && (
            <div>
              <h4 className="report__subtitle">Getting better</h4>
              <ul className="list">
                {report.improving.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

import type { SituationReport } from '../types'

export function OutlookPanel({ report }: { report: SituationReport }) {
  return (
    <section className="panel panel--outlook">
      <header className="panel__head">
        <h2 className="panel__title">Next 30–60 minutes</h2>
        <span className="tag tag--assessment">Assessment, not a certainty</span>
      </header>

      <p className="outlook__text">{report.outlook_30_60min || 'No outlook available.'}</p>

      {report.key_evidence.length > 0 && (
        <details className="outlook__evidence">
          <summary>Evidence this assessment rests on</summary>
          <ul className="list list--muted">
            {report.key_evidence.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
